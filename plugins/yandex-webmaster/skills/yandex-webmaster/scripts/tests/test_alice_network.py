"""Сетевые проверки Алисы: только локальный HTTP и подставные ошибки."""
import contextlib
import importlib.util
import io
import ssl
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "alice_network", Path(__file__).resolve().parents[1] / "alice_efficiency.py"
)
alice = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(alice)


class AliceNetworkTests(unittest.TestCase):
    def setUp(self):
        self.responses = []
        self.requests = []
        responses, requests = self.responses, self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers))
                status, headers, body = responses.pop(0) if responses else (599, {}, b"")
                self.send_response(status)
                self.send_header("Content-Length", str(headers.get("Content-Length", len(body))))
                for name, value in headers.items():
                    if name != "Content-Length":
                        self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.raw = Path(temporary.name) / "raw.html"
        self.raw.write_text("previous response", encoding="utf-8")
        address = f"http://127.0.0.1:{server.server_port}/{{host_id}}/"
        patches = contextlib.ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(mock.patch.object(alice, "ALICE_URL_TEMPLATE", address))
        patches.enter_context(mock.patch("urllib.request.getproxies", return_value={}))
        self.sleep = patches.enter_context(mock.patch("time.sleep"))
        # Если защита от переходов сломается, тест не должен обратиться к Яндексу.
        patches.enter_context(mock.patch.object(
            urllib.request.HTTPSHandler, "https_open", side_effect=AssertionError("External HTTPS request")
        ))

    def fetch(self):
        return alice.fetch_html("test-host", "dummy-cookie-for-tests", str(self.raw))

    def assert_failed(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            self.fetch()
        self.assertNotEqual(error.exception.code, 0)
        self.assertTrue(stderr.getvalue().strip())
        self.assertNotIn("dummy-cookie-for-tests", stderr.getvalue())
        return stderr.getvalue()

    def test_success_saves_html_and_uses_timeout(self):
        html = "<html>Ответ Алисы</html>"
        self.responses.append((200, {"Content-Type": "text/html; charset=windows-1251"}, html.encode("cp1251")))
        original = urllib.request.OpenerDirector.open
        with mock.patch.object(urllib.request.OpenerDirector, "open", autospec=True, side_effect=original) as open_url:
            self.assertEqual(self.fetch(), html)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 30)
        self.assertEqual(self.raw.read_bytes(), html.encode("cp1251"))
        self.assertEqual(self.requests[0][0], "/test-host/")
        self.assertEqual(self.requests[0][1]["Cookie"], "Session_id=dummy-cookie-for-tests")
        self.sleep.assert_not_called()

    def test_retries_temporary_statuses(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.requests.clear()
                self.sleep.reset_mock()
                self.responses.extend([(status, {}, b"temporary"), (200, {}, b"complete")])
                self.assertEqual(self.fetch(), "complete")
                self.assertEqual(len(self.requests), 2)
                self.sleep.assert_called_once()
                self.assertEqual(self.raw.read_text(), "complete")

    def test_stops_after_three_attempts_and_keeps_last_complete_html(self):
        self.responses.extend((503, {}, f"response {i}".encode()) for i in range(3))
        self.assert_failed()
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.raw.read_text(), "response 2")

    def test_retry_after_seconds_and_http_date(self):
        for header, lower, upper in (("3", 3, 3), (formatdate(time.time() + 30, usegmt=True), 25, 30)):
            with self.subTest(header=header):
                self.sleep.reset_mock()
                self.responses.extend([(429, {"Retry-After": header}, b"wait"), (200, {}, b"ok")])
                self.assertEqual(self.fetch(), "ok")
                self.sleep.assert_called_once()
                delay = self.sleep.call_args.args[0]
                self.assertGreaterEqual(delay, lower)
                self.assertLessEqual(delay, upper)

    def test_retry_after_over_one_minute_does_not_retry_early(self):
        for header in ("120", formatdate(time.time() + 120, usegmt=True)):
            with self.subTest(header=header):
                self.requests.clear()
                self.responses.append((429, {"Retry-After": header}, b"wait longer"))
                self.assert_failed()
                self.assertEqual(len(self.requests), 1)
                self.sleep.assert_not_called()
                self.assertEqual(self.raw.read_text(), "wait longer")

    def test_permanent_http_errors_are_saved_without_retry(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                self.requests.clear()
                self.responses.append((status, {}, b"error page"))
                self.assert_failed()
                self.assertEqual(len(self.requests), 1)
                self.sleep.assert_not_called()
                self.assertEqual(self.raw.read_text(), "error page")

    def test_redirect_is_saved_but_never_followed(self):
        for location in ("https://passport.yandex.ru/auth", "/showcaptcha", "/somewhere-else"):
            with self.subTest(location=location):
                self.requests.clear()
                self.responses.append((302, {"Location": location}, b"redirect page"))
                self.assert_failed()
                self.assertEqual(len(self.requests), 1)
                self.sleep.assert_not_called()
                self.assertEqual(self.raw.read_text(), "redirect page")

    def test_captcha_page_is_saved_without_retry(self):
        html = '<form action="/showcaptcha"><input name="rep"></form>'
        self.responses.append((200, {}, html.encode()))
        self.assert_failed()
        self.assertEqual(len(self.requests), 1)
        self.sleep.assert_not_called()
        self.assertEqual(self.raw.read_text(), html)

    def test_partial_response_does_not_replace_previous_html(self):
        self.responses.extend((200, {"Content-Length": "100"}, b"partial") for _ in range(3))
        self.assert_failed()
        self.assertEqual(self.raw.read_text(), "previous response")
        self.assertEqual(len(self.requests), 3)

    def test_temporary_network_errors_have_bounded_retries(self):
        for error in (TimeoutError("timeout"), urllib.error.URLError(ConnectionResetError("reset"))):
            with self.subTest(error=error):
                self.sleep.reset_mock()
                with mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=error) as open_url:
                    self.assert_failed()
                self.assertEqual(open_url.call_count, 3)
                self.assertEqual(self.sleep.call_count, 2)
                self.assertEqual(self.raw.read_text(), "previous response")

    def test_certificate_errors_are_not_retried(self):
        certificate_error = ssl.SSLCertVerificationError("certificate verify failed")
        for error in (certificate_error, urllib.error.URLError(certificate_error)):
            with self.subTest(error=error):
                with mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=error) as open_url:
                    self.assert_failed()
                self.assertEqual(open_url.call_count, 1)
                self.sleep.assert_not_called()
                self.assertEqual(self.raw.read_text(), "previous response")


if __name__ == "__main__":
    unittest.main()
