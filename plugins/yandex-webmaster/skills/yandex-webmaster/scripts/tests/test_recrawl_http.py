"""HTTP-ответы Вебмастера через прокси и напрямую."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
CONNECT = "HTTP/1.1 200 Connection established\r\n\r\n"


class RecrawlHttpTests(unittest.TestCase):
    def run_recrawl(self, action: str, *responses: tuple[str, str]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            scripts.mkdir()
            for name in ("common.sh", "recrawl.sh"):
                shutil.copyfile(SCRIPTS / name, scripts / name)
            (root / "cache").mkdir()
            (root / "cache/user_id.txt").write_text("123")
            for index, (headers, body) in enumerate(responses):
                (root / f"reply{index}.headers").write_bytes(headers.encode())
                (root / f"reply{index}.body").write_text(body)

            curl = root / "curl"
            curl.write_text('''#!/bin/sh
set -e
while [ "$#" -gt 1 ] && [ "$1" != "-D" ]; do shift; done
[ "$1" = "-D" ]
for headers in "$TMPDIR"/reply*.headers; do
    mv "$headers" "$2"
    cat "${headers%.headers}.body"
    exit 0
done
''')
            curl.chmod(0o755)
            extra = ["--url", "https://example.org/page"] if action == "submit" else []
            return subprocess.run(
                ["/bin/sh", str(scripts / "recrawl.sh"), "--host-id", "host", "--action", action, *extra],
                env={"TMPDIR": temporary, "PATH": temporary + os.pathsep + os.defpath,
                     "YANDEX_WEBMASTER_TOKEN": "test-token"},
                capture_output=True, text=True, timeout=10,
            )

    def test_proxy_success_does_not_hide_api_error(self) -> None:
        for action, status in (("submit", "HTTP/1.1 504 Gateway Timeout"), ("quota", "HTTP/2 504")):
            with self.subTest(action=action):
                result = self.run_recrawl(action, (CONNECT + status + "\r\n\r\n", ""))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("Error: HTTP 504", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_proxy_rate_limit_retries_submit(self) -> None:
        result = self.run_recrawl(
            "submit",
            (CONNECT + "HTTP/2 429\r\nRetry-After: 0\r\n\r\n", ""),
            (CONNECT + "HTTP/2 202\r\n\r\n", '{"task_id":"task-123"}'),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("task-123", result.stdout)

    def test_quota_with_and_without_proxy(self) -> None:
        for prefix in ("", CONNECT):
            with self.subTest(proxy=bool(prefix)):
                result = self.run_recrawl("quota", (prefix + "HTTP/2 200\r\n\r\n", '{"daily_quota":100}'))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Daily quota: 100", result.stdout)
