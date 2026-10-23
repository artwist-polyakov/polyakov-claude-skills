#!/usr/bin/env python3
"""Offline regression tests for OAuth transport and query cache isolation."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
TOKEN = "synthetic-webmaster-test-token"


def fake_curl(args):
    """No network: record request metadata, never the Authorization value."""
    log = Path(os.environ["TEST_CURL_LOG"])
    requests = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    headers = None
    body = None
    auth_file = None
    private = False
    auth_ok = False
    for index, arg in enumerate(args[:-1]):
        value = args[index + 1]
        if arg == "-D":
            headers = Path(value)
        elif arg == "-d":
            body = json.loads(value)
        elif arg == "-H" and value.startswith("@"):
            auth_file = Path(value[1:])
            private = not (auth_file.stat().st_mode & 0o077)
            auth_ok = auth_file.read_text().splitlines() == [f"Authorization: OAuth {TOKEN}"]
    request = {
        "url": args[-1], "body": body,
        "auth_in_argv": any(TOKEN in arg for arg in args),
        "auth_in_env": bool(os.environ.get("YANDEX_WEBMASTER_TOKEN")),
        "auth_file": str(auth_file) if auth_file else None,
        "private": private, "auth_ok": auth_ok,
        "curlrc_disabled": args[0] == "-q",
    }
    with log.open("a") as stream:
        stream.write(json.dumps(request) + "\n")
    if os.environ.get("TEST_CURL_EXIT"):
        return int(os.environ["TEST_CURL_EXIT"])
    statuses = os.environ.get("TEST_HTTP_STATUSES", "200").split(",")
    status = statuses[min(len(requests), len(statuses) - 1)]
    headers.write_text(f"HTTP/1.1 {status} Test\r\nRetry-After: 0\r\n\r\n")
    if os.environ.get("TEST_CURL_SIGNAL"):
        os.kill(os.getppid(), signal.SIGTERM)
        return 0
    if int(status) >= 400:
        print('{"error_code":"TEST_ERROR","error_message":"synthetic error"}')
    elif args[-1].endswith("/query-analytics/list"):
        print(json.dumps({"count": 1, "queries": [{
            "text_indicator": {"value": f"row-{len(requests) + 1}"},
            "statistics": [{"field": "IMPRESSIONS", "value": 1}],
        }]}, separators=(",", ":")))
    elif args[-1].endswith("/search-queries/popular"):
        print('{"queries":[{"query_id":"test","query_text":"example","TOTAL_SHOWS":1}]}')
    else:
        print('{"ok":true}')
    return 0


class WebmasterRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="webmaster-regression-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("common.sh", "query_analytics.sh", "popular_queries.sh"):
            shutil.copyfile(SCRIPTS / name, self.scripts / name)
        (self.scripts / "probe.sh").write_text('''#!/bin/sh
. "$(dirname "$0")/common.sh"
load_config
trap 'printf done > "$TEST_CALLER_EXIT"' 0
case "$1" in
    get) webmaster_raw_get "/v4/user" ;;
    post) webmaster_raw_post "/v4/test" '{"test":true}' ;;
esac
''')
        cache = self.root / "cache"
        cache.mkdir()
        (cache / "user_id.txt").write_text("123")
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        self.log = self.root / "curl.jsonl"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name in ("dirname", "mkdir", "cat", "grep", "head", "sed", "cut", "tr",
                     "wc", "rm", "cp", "awk", "cksum", "find", "mktemp"):
            (bin_dir / name).symlink_to(shutil.which(name))
        (bin_dir / "curl").write_text(
            f"#!{sys.executable}\nimport runpy, sys\n"
            f"module = runpy.run_path({str(Path(__file__).resolve())!r})\n"
            "sys.exit(module['fake_curl'](sys.argv[1:]))\n"
        )
        (bin_dir / "curl").chmod(0o755)
        (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / "sleep").chmod(0o755)
        self.env = {
            "PATH": str(bin_dir), "LC_ALL": "C", "PYTHONUTF8": "1",
            "TMPDIR": str(self.tmp), "YANDEX_WEBMASTER_TOKEN": TOKEN,
            "TEST_CURL_LOG": str(self.log), "TEST_CALLER_EXIT": str(self.root / "caller-exit"),
        }

    def run_script(self, name, *args):
        return subprocess.run(
            ["/bin/sh", str(self.scripts / name), *args], env=self.env,
            capture_output=True, text=True, timeout=10,
        )

    def requests(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_private_requests(self):
        for request in self.requests():
            self.assertFalse(request["auth_in_argv"])
            self.assertFalse(request["auth_in_env"])
            self.assertTrue(request["private"])
            self.assertTrue(request["auth_ok"])
            self.assertTrue(request["curlrc_disabled"])
            self.assertFalse(Path(request["auth_file"]).exists())
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_get_and_post_keep_oauth_out_of_argv_and_environment(self):
        for method in ("get", "post"):
            result = self.run_script("probe.sh", method)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertEqual(len(self.requests()), 2)
        self.assert_private_requests()

    def test_http_and_network_errors_clean_up_private_headers(self):
        for method in ("get", "post"):
            for failure in ("http", "network"):
                with self.subTest(method=method, failure=failure):
                    self.env.pop("TEST_CURL_EXIT", None)
                    self.env.pop("TEST_HTTP_STATUSES", None)
                    self.env["TEST_HTTP_STATUSES" if failure == "http" else "TEST_CURL_EXIT"] = (
                        "403" if failure == "http" else "7"
                    )
                    result = self.run_script("probe.sh", method)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn(TOKEN, result.stdout + result.stderr)
                    self.assert_private_requests()

    def test_rate_limit_retry_uses_fresh_private_headers(self):
        for method in ("get", "post"):
            self.log.unlink(missing_ok=True)
            self.env["TEST_HTTP_STATUSES"] = "429,200"
            result = self.run_script("probe.sh", method)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(self.requests()), 2)
            paths = [request["auth_file"] for request in self.requests()]
            self.assertEqual(len(set(paths)), 2)
            self.assert_private_requests()

    def test_repeated_rate_limit_stops_after_one_retry(self):
        self.env["TEST_HTTP_STATUSES"] = "429"
        for method in ("get", "post"):
            self.log.unlink(missing_ok=True)
            result = self.run_script("probe.sh", method)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len(self.requests()), 2)
            self.assert_private_requests()

    def test_signal_cleans_headers_and_preserves_callers_exit_trap(self):
        self.env["TEST_CURL_SIGNAL"] = "1"
        marker = self.root / "caller-exit"
        for method in ("get", "post"):
            marker.unlink(missing_ok=True)
            result = self.run_script("probe.sh", method)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(), "done")
            self.assert_private_requests()

    def test_failed_auth_file_creation_cleans_response_headers(self):
        wrapper = self.root / "bin" / "mktemp"
        wrapper.unlink()
        wrapper.write_text(
            '#!/bin/sh\ncase "$1" in *wm_auth*) exit 1 ;; esac\n'
            f'exec "{shutil.which("mktemp")}" "$@"\n'
        )
        wrapper.chmod(0o755)
        for method in ("get", "post"):
            result = self.run_script("probe.sh", method)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.requests(), [])
            self.assertEqual(list(self.tmp.iterdir()), [])

    def test_invalid_token_cannot_inject_headers(self):
        for token in ("", "test\nX-Injected: true", "test\rX-Injected: true", "test\tvalue"):
            self.env["YANDEX_WEBMASTER_TOKEN"] = token
            for method in ("get", "post"):
                with self.subTest(method=method, token=repr(token)):
                    result = self.run_script("probe.sh", method)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.requests(), [])
                    self.assertEqual(list(self.tmp.iterdir()), [])

    def test_query_cache_includes_entire_request(self):
        variants = (
            (), ("--offset", "500"), ("--limit", "10"), ("--region-ids", "213"),
            ("--filter-impressions", ">100"), ("--filter-clicks", "<50"),
            ("--filter-position", "<10"), ("--filter-ctr", ">0.05"),
            ("--filter-demand", ">1000"), ("--search-location", "ALL_LOCATIONS"),
            ("--sort-by", "ASC"), ("--device", "DESKTOP"),
            ("--text-indicator", "URL"), ("--filter-text", "example"),
        )
        base_args = ("--host-id", "https:example.com:443")
        for index, variant in enumerate(variants, 1):
            with self.subTest(args=variant):
                result = self.run_script("query_analytics.sh", *base_args, *variant)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(self.requests()), index)
                self.assertIn(f"row-{index}\t", result.stdout)
                cached = self.run_script("query_analytics.sh", *base_args, *variant)
                self.assertEqual(cached.returncode, 0, cached.stderr)
                self.assertIn("(cached:", cached.stdout)
                self.assertEqual(len(self.requests()), index)
        bodies = [json.dumps(request["body"], sort_keys=True) for request in self.requests()]
        self.assertEqual(len(set(bodies)), len(variants))

    def test_no_cache_bypasses_saved_query_report(self):
        args = ("--host-id", "https:example.com:443")
        first = self.run_script("query_analytics.sh", *args)
        fresh = self.run_script("query_analytics.sh", *args, "--no-cache")
        cached = self.run_script("query_analytics.sh", *args)
        for result in (first, fresh, cached):
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.requests()), 2)
        self.assertIn("row-2\t", fresh.stdout)
        self.assertIn("row-2\t", cached.stdout)

    def test_legacy_query_cache_is_not_reused(self):
        query_dir = self.root / "cache" / "host_https_example.com_443" / "queries"
        query_dir.mkdir(parents=True)
        checksum = subprocess.run(
            ["cksum"], input="analytics_QUERY__ALL", capture_output=True, text=True, check=True,
        ).stdout.split()[0]
        (query_dir / f"analytics_{checksum}.tsv").write_text("wrong legacy report\n")
        result = self.run_script("query_analytics.sh", "--host-id", "https:example.com:443")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.requests()), 1)
        self.assertNotIn("wrong legacy report", result.stdout)

    def test_popular_query_cache_includes_limit(self):
        args = ("--host-id", "https:example.com:443")
        for index, limit in enumerate(("10", "100"), 1):
            result = self.run_script("popular_queries.sh", *args, "--limit", limit)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(self.requests()), index)
            cached = self.run_script("popular_queries.sh", *args, "--limit", limit)
            self.assertEqual(cached.returncode, 0, cached.stderr)
            self.assertIn("(cached:", cached.stdout)
            self.assertEqual(len(self.requests()), index)


if __name__ == "__main__":
    unittest.main()
