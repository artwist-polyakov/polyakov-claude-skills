#!/usr/bin/env python3
"""Поиск IDN-доменов: временный кеш и подставной API, без аккаунта."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
DOMAINS = (
    ("хорошие-данные.рф", "xn----8sblcdn3bacav2d0bxc.xn--p1ai"),
    ("так-вижу.рф", "xn----7sbhqjj8co.xn--p1ai"),
)


class HostSearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("common.sh", "hosts.sh"):
            shutil.copyfile(SCRIPTS_DIR / name, self.scripts / name)
        with (self.scripts / "common.sh").open("a", encoding="utf-8") as script:
            script.write('''
webmaster_raw_get() {
    printf 'fetch\\n' >> "$TEST_API_LOG"
    cat "$TEST_API_RESPONSE"
}
''')
        (self.scripts / "probe.sh").write_text('''#!/bin/sh
. "$(dirname "$0")/common.sh"
case "$1" in
    normalize) normalize_host_search "$2" ;;
    resolve)
        HOST_SEARCH="$2"
        HOST_ID="${3:-}"
        USER_ID=123
        resolve_host
        printf '%s\\n' "$HOST_ID"
        ;;
esac
''', encoding="utf-8")
        cache_dir = self.root / "cache"
        cache_dir.mkdir()
        self.cache = cache_dir / "hosts.tsv"
        (cache_dir / "user_id.txt").write_text("123", encoding="utf-8")
        hosts = [
            {"host_id": f"https:{domain}:443", "ascii_host_url": f"https://{domain}/", "verified": True}
            for domain in [pair[1] for pair in DOMAINS] + ["example.com"]
        ]
        self.cache.write_text("".join(
            f"{host['host_id']}\t{host['ascii_host_url']}\ttrue\n" for host in hosts
        ), encoding="utf-8")
        response = self.root / "response.json"
        response.write_text(json.dumps({"hosts": hosts}, separators=(",", ":")), encoding="utf-8")
        self.api_log = self.root / "api.log"
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        # Изолированный PATH позволяет действительно убрать Python и исключает curl.
        for name in ("dirname", "mkdir", "cat", "grep", "head", "sed", "cut", "tr", "wc", "rm", "cp"):
            (self.bin_dir / name).symlink_to(shutil.which(name))
        self.python = self.bin_dir / "python3"
        self.python.symlink_to(sys.executable)
        self.environment = {
            "PATH": str(self.bin_dir), "LC_ALL": "C", "PYTHONUTF8": "1",
            "YANDEX_WEBMASTER_TOKEN": "test-token", "TMPDIR": str(self.root / "tmp"),
            "TEST_API_RESPONSE": str(response), "TEST_API_LOG": str(self.api_log),
        }

    def run_script(self, name, *args):
        return subprocess.run(
            ["/bin/sh", str(self.scripts / name), *args], env=self.environment,
            capture_output=True, text=True, timeout=10,
        )

    def test_ascii_search_stays_literal_without_python(self):
        self.python.unlink()
        for term in ("example.com", "exam", "HTTPS://Example.COM:443/", DOMAINS[0][1]):
            with self.subTest(term=term):
                result = self.run_script("probe.sh", "normalize", term)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.rstrip("\n"), term)
        for name, args in (
            ("probe.sh", ("resolve", "exam")),
            ("hosts.sh", ("--search", "EXAM")),
        ):
            result = self.run_script(name, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("https:example.com:443", result.stdout)
        self.assertFalse(self.api_log.exists())

    def test_full_unicode_domains_use_idna(self):
        for unicode_domain, ascii_domain in DOMAINS:
            with self.subTest(domain=unicode_domain):
                result = self.run_script("probe.sh", "normalize", unicode_domain)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), ascii_domain)

    def test_url_changes_only_hostname(self):
        unicode_domain, ascii_domain = DOMAINS[0]
        urls = (
            (f"https://{unicode_domain}/", f"https://{ascii_domain}/"),
            (f"https://{unicode_domain.upper()}:8443/path?q=1#part", f"https://{ascii_domain}:8443/path?q=1#part"),
            (f"{unicode_domain}/", f"{ascii_domain}/"),
            (f"{unicode_domain}:8443/path", f"{ascii_domain}:8443/path"),
        )
        for original, expected in urls:
            with self.subTest(url=original):
                result = self.run_script("probe.sh", "normalize", original)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_resolve_host_accepts_unicode_domains_and_root_urls(self):
        for unicode_domain, ascii_domain in DOMAINS:
            for term in (unicode_domain, f"https://{unicode_domain}/", f"HTTPS://{unicode_domain.upper()}/", f"{unicode_domain}/"):
                with self.subTest(term=term):
                    result = self.run_script("probe.sh", "resolve", term)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), f"https:{ascii_domain}:443")
        self.assertFalse(self.api_log.exists())

    def test_hosts_search_accepts_unicode_with_cached_and_fetched_data(self):
        for unicode_domain, ascii_domain in DOMAINS:
            for refresh in (False, True):
                with self.subTest(domain=unicode_domain, refresh=refresh):
                    args = ["--search", f"https://{unicode_domain}/"]
                    if refresh:
                        args.append("--no-cache")
                    result = self.run_script("hosts.sh", *args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"https:{ascii_domain}:443", result.stdout)
                    self.assertNotIn("https:example.com:443", result.stdout)
                    self.assertNotIn("no matches", result.stdout)
        self.assertEqual(self.api_log.read_text().splitlines(), ["fetch", "fetch"])

    def test_resolve_host_refreshes_missing_cache_before_search(self):
        self.cache.unlink()

        result = self.run_script("probe.sh", "resolve", DOMAINS[1][0])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"https:{DOMAINS[1][1]}:443")
        self.assertEqual(self.api_log.read_text().splitlines(), ["fetch"])
        self.assertIn(DOMAINS[1][1], self.cache.read_text())

    def test_explicit_host_id_skips_normalization_and_fetch(self):
        self.python.unlink()
        self.cache.unlink()
        host_id = "http:example.com:80"

        result = self.run_script("probe.sh", "resolve", ".рф", host_id)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), host_id)
        self.assertFalse(self.api_log.exists())

    def test_missing_python_is_an_error_for_unicode_search(self):
        self.python.unlink()
        for name, args in (
            ("probe.sh", ("normalize", DOMAINS[0][0])),
            ("probe.sh", ("resolve", DOMAINS[0][0])),
            ("hosts.sh", ("--search", DOMAINS[0][0])),
        ):
            with self.subTest(script=name, args=args):
                result = self.run_script(name, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("python", result.stderr.lower())
                self.assertNotIn("no match", (result.stdout + result.stderr).lower())

    def test_invalid_idna_is_an_error_in_both_search_paths(self):
        for name, args in (
            ("probe.sh", ("normalize", ".рф")),
            ("probe.sh", ("resolve", ".рф")),
            ("hosts.sh", ("--search", ".рф")),
        ):
            with self.subTest(script=name, args=args):
                result = self.run_script(name, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("IDNA", result.stderr)
                self.assertNotIn("no match", (result.stdout + result.stderr).lower())
                self.assertNotIn("Traceback", result.stderr)

    def test_no_match_message_keeps_original_unicode_search(self):
        term = "несуществующий.рф"
        result = self.run_script("probe.sh", "resolve", term)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(term, result.stderr)
        self.assertIn("no host matching", result.stderr)

        result = self.run_script("hosts.sh", "--search", term)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(term, result.stdout)
        self.assertIn("no matches", result.stdout)

    def test_existing_grep_pattern_semantics_are_preserved(self):
        for name, args in (
            ("probe.sh", ("resolve", "example[.]com")),
            ("hosts.sh", ("--search", "example[.]com")),
        ):
            result = self.run_script(name, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("https:example.com:443", result.stdout)


if __name__ == "__main__":
    unittest.main()
