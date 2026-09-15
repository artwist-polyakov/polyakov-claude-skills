"""Переключение адреса API не подменяет кеш и выбранный кабинет."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import accounts
import cache
from config import DEFAULT_API_BASE_URL, DEFAULT_API_BASE_URL_V4


def settings(base=DEFAULT_API_BASE_URL, base_v4=DEFAULT_API_BASE_URL_V4, extra_headers=None):
    return SimpleNamespace(base_url=base, base_url_v4=base_v4,
                           extra_headers=extra_headers or {},
                           token="same-test-credential", profile="production",
                           account="")


class ProxyCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        changed_root = patch.object(cache, "CACHE_DIR", self.root)
        changed_root.start()
        self.addCleanup(changed_root.stop)
        self.direct = settings()
        self.proxy = settings("https://proxy.example/direct",
                              "https://proxy.example/direct")

    def test_direct_keeps_existing_cache_and_proxy_has_its_own_data(self):
        legacy = cache.Cache("advertiser")
        legacy.write("campaigns", "structure", [{"Id": 1}])
        direct = cache.Cache("advertiser", settings=self.direct)
        proxy = cache.Cache("advertiser", settings=self.proxy, warn=lambda message: None)

        self.assertEqual(direct.path("campaigns"), self.root / "advertiser/campaigns.json")
        self.assertEqual(direct.read("campaigns", "structure").data, [{"Id": 1}])
        self.assertIsNone(proxy.read("campaigns", "structure"))
        proxy.write("campaigns", "structure", [{"Id": 2}])
        self.assertEqual(proxy.read("campaigns", "structure").data, [{"Id": 2}])
        self.assertEqual(direct.read("campaigns", "structure").data, [{"Id": 1}])
        proxy.forget("campaigns")
        self.assertIsNotNone(direct.read("campaigns", "structure"))

    def test_paths_on_one_host_and_v4_base_are_part_of_cache_key(self):
        variants = [
            self.direct,
            self.proxy,
            settings("https://proxy.example/other", "https://proxy.example/direct"),
            settings("https://proxy.example/direct", "https://proxy.example/other"),
            settings(DEFAULT_API_BASE_URL, "https://proxy.example/direct"),
        ]
        stores = [cache.Cache(settings=value) for value in variants]
        self.assertEqual(len({store.path("accounts") for store in stores}), len(stores))
        self.assertEqual(stores[1].root, self.root)
        self.assertIn("proxy.example-", str(stores[1].path("accounts")))
        self.assertEqual(cache.Cache(settings=self.proxy).path("accounts"),
                         stores[1].path("accounts"))

    def test_account_lists_and_active_selection_survive_switching_back(self):
        direct_client = SimpleNamespace(settings=self.direct)
        proxy_client = SimpleNamespace(settings=self.proxy)

        def detect(client):
            suffix = "direct" if client is direct_client else "proxy"
            return (accounts.AGENCY, suffix, [accounts.Cabinet("shared"),
                                             accounts.Cabinet(suffix)])

        with patch.object(accounts, "detect", side_effect=detect) as fetched, \
                patch.object(accounts, "balances", return_value=({}, [])):
            direct = accounts.Accounts.load(direct_client)
            direct.select(direct.by_login("direct"))
            proxy = accounts.Accounts.load(proxy_client)
            self.assertEqual([one.login for one in proxy.cabinets], ["shared", "proxy"])
            self.assertIsNone(proxy.current())
            proxy.select(proxy.by_login("shared"))

            direct_again = accounts.Accounts.load(direct_client)
            proxy_again = accounts.Accounts.load(proxy_client)
            self.assertEqual(direct_again.current().login, "direct")
            self.assertEqual(proxy_again.current().login, "shared")
            self.assertEqual(fetched.call_count, 2)
            self.assertEqual(direct.cache.path("accounts"), self.root / "accounts.json")

    def test_management_lists_and_clears_account_across_connections(self):
        stores = [cache.Cache("advertiser", settings=value)
                  for value in (self.direct, self.proxy)]
        for index, store in enumerate(stores):
            store.write("campaigns", "structure", [{"Id": index}])
        other = cache.Cache("another", settings=self.proxy)
        other.write("campaigns", "structure", [{"Id": 3}])
        manager = cache.Cache("advertiser")
        entries = manager.entries(all_connections=True)
        self.assertEqual(len(entries), 2)
        self.assertEqual({one["folder"] for one in entries}, {"advertiser"})
        self.assertEqual(len({one["connection"] for one in entries}), 2)
        manager.forget(all_connections=True)
        for store in stores:
            self.assertIsNone(store.read("campaigns", "structure"))
        self.assertIsNotNone(other.read("campaigns", "structure"))

    def test_proxy_headers_separate_account_lists_and_active_selection(self):
        def client(owner):
            return SimpleNamespace(settings=settings(
                self.proxy.base_url, self.proxy.base_url_v4,
                {"X-Token-Login": owner, "X-Route": "shared"}))

        first_client, second_client = client("first-owner"), client("second-owner")

        def detect(client):
            owner = client.settings.extra_headers["X-Token-Login"]
            return accounts.AGENCY, owner, [accounts.Cabinet(owner), accounts.Cabinet("shared")]

        with patch.object(accounts, "detect", side_effect=detect) as fetched, \
                patch.object(accounts, "balances", return_value=({}, [])):
            first = accounts.Accounts.load(first_client)
            first.select(first.by_login("first-owner"))
            second = accounts.Accounts.load(second_client)
            self.assertEqual([one.login for one in second.cabinets], ["second-owner", "shared"])
            self.assertIsNone(second.current())
            second.select(second.by_login("shared"))

            # Регистр имён и порядок заголовков не меняют подключение.
            first_client.settings.extra_headers = {
                "x-route": "shared", "x-token-login": "first-owner"}
            self.assertEqual(accounts.Accounts.load(first_client).current().login, "first-owner")
            self.assertEqual(accounts.Accounts.load(second_client).current().login, "shared")
            self.assertEqual(fetched.call_count, 2)
            self.assertNotIn("first-owner", str(first.cache.path("accounts")))
            self.assertNotIn("second-owner", str(second.cache.path("accounts")))

    def test_whole_cache_clear_revokes_pending_proxy_write(self):
        proxy = cache.Cache("advertiser", settings=self.proxy, warn=lambda message: None)
        proxy.write("campaigns", "structure", [{"Id": 1}])
        since = proxy._marks("campaigns")
        cache.Cache().forget(everything=True)
        entry = proxy.write("campaigns", "structure", [{"Id": 2}], since=since)
        self.assertIsNone(entry.path)
        self.assertIsNone(proxy.read("campaigns", "structure"))


if __name__ == "__main__":
    unittest.main()
