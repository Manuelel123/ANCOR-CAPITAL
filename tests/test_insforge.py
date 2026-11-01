"""InsForge REST client and the persistent price store (no network)."""

import time
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pandas as pd
import requests

from optimizacion_portafolios.data import market_data, price_store
from optimizacion_portafolios.insforge import (
    InsForgeClient,
    InsForgeError,
    InsForgeSettings,
    PAGE_SIZE,
    in_filter,
)
from fake_insforge import FakeInsForge


def _response(status=200, body=None):
    response = mock.Mock()
    response.status_code = status
    response.reason = "Error"
    response.content = b"" if body is None else b"x"
    response.json.return_value = body
    return response


class InsForgeClientTests(unittest.TestCase):
    def setUp(self):
        self.http = mock.Mock(spec=requests.Session)
        self.client = InsForgeClient(
            InsForgeSettings("https://app.insforge.app", "api-key"),
            session=self.http,
        )

    def test_http_errors_keep_status_and_code(self):
        self.http.request.return_value = _response(
            401, {"error": "AUTH_UNAUTHORIZED", "message": "Invalid token"}
        )
        with self.assertRaises(InsForgeError) as caught:
            self.client.select("price_history")
        self.assertEqual((caught.exception.status, caught.exception.code), (401, "AUTH_UNAUTHORIZED"))

    def test_network_error_has_no_status(self):
        self.http.request.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(InsForgeError) as caught:
            self.client.select("price_history")
        self.assertIsNone(caught.exception.status)

    def test_settings_require_url_and_api_key(self):
        with mock.patch.dict("os.environ", {"INSFORGE_URL": "https://x.app", "INSFORGE_API_KEY": ""}):
            self.assertIsNone(InsForgeSettings.from_env())
        with mock.patch.dict("os.environ", {"INSFORGE_URL": "https://x.app/", "INSFORGE_API_KEY": "k"}):
            self.assertEqual(InsForgeSettings.from_env(), InsForgeSettings("https://x.app", "k"))

    def test_client_has_no_auth_methods(self):
        for name in ("sign_in", "sign_up", "refresh", "oauth_url", "public_config", "update"):
            self.assertFalse(hasattr(self.client, name), name)

    def test_select_pages_past_the_postgrest_cap(self):
        first = [{"n": index} for index in range(PAGE_SIZE)]
        self.http.request.side_effect = [_response(body=first), _response(body=[{"n": -1}])]
        rows = self.client.select("price_history", {"ticker": "eq.AAPL"}, admin=True)
        self.assertEqual(len(rows), PAGE_SIZE + 1)
        offsets = [call.kwargs["params"]["offset"] for call in self.http.request.call_args_list]
        self.assertEqual(offsets, [0, PAGE_SIZE])
        self.assertEqual(
            self.http.request.call_args.kwargs["headers"]["Authorization"], "Bearer api-key"
        )

    def test_missing_api_key_is_rejected_before_any_request(self):
        client = InsForgeClient(InsForgeSettings("https://app.insforge.app", None), session=self.http)
        with self.assertRaises(InsForgeError):
            client.select("price_history")
        self.http.request.assert_not_called()

    def test_upsert_sets_prefer_header_and_conflict_columns(self):
        self.http.request.return_value = _response(204)
        self.client.insert(
            "price_history", [{"ticker": "A"}], admin=True, upsert=True,
            on_conflict="ticker,day", returning=False,
        )
        kwargs = self.http.request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Prefer"], "return=minimal,resolution=merge-duplicates")
        self.assertEqual(kwargs["params"], {"on_conflict": "ticker,day"})

    def test_deadline_caps_the_timeout_and_rejects_expired_calls(self):
        self.http.request.return_value = _response(body=[])
        self.client.select("price_history", deadline=time.monotonic() + 2)
        self.assertLessEqual(self.http.request.call_args.kwargs["timeout"], 2)
        self.http.request.reset_mock()
        with self.assertRaises(InsForgeError):
            self.client.select("price_history", deadline=time.monotonic() - 1)
        self.http.request.assert_not_called()

    def test_in_filter_quotes_values(self):
        self.assertEqual(in_filter(["GC=F", "^GSPC"]), 'in.("GC=F","^GSPC")')


class PriceStoreTests(unittest.TestCase):
    """Incremental refresh: stored, tail and full downloads per ticker."""

    def setUp(self):
        self.fake = FakeInsForge()
        price_store.configure(self.fake, full_refresh_days=7, intraday_ttl_minutes=60)
        market_data.clear_price_cache()
        self.addCleanup(price_store.configure, None)
        self.addCleanup(market_data.clear_price_cache)
        self.start, self.end = date(2025, 1, 1), date(2025, 3, 31)
        self.scale = 1.0
        self.downloads = []

    def _download(self, tickers, start, end):
        """Deterministic provider: one close per business day, scaled by ``self.scale``."""
        self.downloads.append((tuple(tickers), start, end))
        index = pd.bdate_range(start, end, name="Fecha")
        return pd.DataFrame(
            {ticker: [self.scale * (100 + day.toordinal() % 97 + offset) for day in index]
             for offset, ticker in enumerate(tickers)},
            index=index,
        )

    def _fetch(self, tickers=("AAA", "BBB"), start=None, end=None):
        return price_store.fetch(tickers, start or self.start, end or self.end, self._download)

    def _coverage(self, ticker):
        return next(row for row in self.fake.tables["price_coverage"] if row["ticker"] == ticker)

    def _age(self, ticker, field, **delta):
        self._coverage(ticker)[field] = (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()

    def test_disabled_without_admin_key(self):
        price_store.configure(FakeInsForge(admin_key=None))
        self.assertFalse(price_store.enabled())
        self.assertIsNone(self._fetch())

    def test_first_request_downloads_and_the_next_one_reads_the_store(self):
        first = self._fetch()
        self.assertEqual(self.downloads, [(("AAA", "BBB"), self.start, self.end)])
        second = self._fetch()
        self.assertEqual(len(self.downloads), 1)
        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(list(first.columns), ["AAA", "BBB"])
        self.assertEqual(len(first), len(pd.bdate_range(self.start, self.end)))

    def test_only_the_recent_tail_is_downloaded(self):
        self._fetch()
        later = date(2025, 4, 15)
        prices = self._fetch(end=later)
        self.assertEqual(self.downloads[-1], (("AAA", "BBB"), self.end - timedelta(days=7), later))
        self.assertEqual(len(self.downloads), 2)
        self.assertEqual(prices.index.max(), pd.Timestamp("2025-04-15"))
        self.assertEqual(self._coverage("AAA")["end_date"], "2025-04-15")
        expected = self._download(("AAA", "BBB"), self.start, later)
        pd.testing.assert_frame_equal(prices, expected, check_freq=False)

    def test_readjusted_overlap_triggers_a_full_download(self):
        self._fetch()
        self.scale = 0.99  # a dividend re-adjusted every past close
        prices = self._fetch(end=date(2025, 4, 15))
        tail, full = self.downloads[-2], self.downloads[-1]
        self.assertEqual(tail[1], self.end - timedelta(days=7))
        self.assertEqual(full, (("AAA", "BBB"), self.start, date(2025, 4, 15)))
        expected = self._download(("AAA", "BBB"), self.start, date(2025, 4, 15))
        pd.testing.assert_frame_equal(prices, expected, check_freq=False)

    def test_weekly_full_refresh(self):
        self._fetch()
        self._age("AAA", "full_refreshed_at", days=8)
        self._fetch()
        self.assertEqual(self.downloads[-1], (("AAA",), self.start, self.end))
        self.assertEqual(len(self.downloads), 2)

    def test_earlier_start_downloads_the_whole_range(self):
        self._fetch()
        self._fetch(start=date(2024, 12, 1))
        self.assertEqual(self.downloads[-1], (("AAA", "BBB"), date(2024, 12, 1), self.end))
        self.assertEqual(self._coverage("AAA")["start_date"], "2024-12-01")

    def test_range_reaching_today_refreshes_the_tail_after_the_intraday_ttl(self):
        today = date.today()
        start = today - timedelta(days=60)
        self._fetch(("AAA",), start, today)
        self._fetch(("AAA",), start, today)
        self.assertEqual(len(self.downloads), 1)
        self._age("AAA", "fetched_at", hours=2)
        self._fetch(("AAA",), start, today)
        self.assertEqual(self.downloads[-1], (("AAA",), today - timedelta(days=7), today))

    def test_tickers_are_planned_independently(self):
        self._fetch(("AAA",))
        self._fetch(("AAA", "BBB"))
        self.assertEqual(self.downloads[-1], (("BBB",), self.start, self.end))

    def test_store_failures_fall_back_to_direct_downloads(self):
        with mock.patch.object(self.fake, "select", side_effect=InsForgeError("down", 503)):
            self.assertIsNone(self._fetch())
        with mock.patch.object(self.fake, "select", side_effect=InsForgeError("down", 503)), \
                mock.patch.object(market_data, "_download_prices_uncached", side_effect=self._download):
            prices = market_data.download_prices(("AAA",), self.start, self.end)
        self.assertEqual(list(prices.columns), ["AAA"])

    def test_a_store_slower_than_the_budget_is_skipped_for_a_while(self):
        price_store.configure(self.fake, budget_seconds=0.05)
        real_select = self.fake.select

        def slow_select(*args, **kwargs):
            time.sleep(0.1)
            return real_select(*args, **kwargs)

        with mock.patch.object(self.fake, "select", side_effect=slow_select):
            self.assertIsNone(self._fetch())
        calls = len(self.fake.calls)
        self.assertIsNone(self._fetch())
        self.assertEqual(len(self.fake.calls), calls)
        price_store.configure(self.fake)
        self.assertIsNotNone(self._fetch())

    def test_provider_downloads_do_not_use_up_the_budget(self):
        price_store.configure(self.fake, budget_seconds=0.05)
        download = self._download

        def slow_download(*args):
            time.sleep(0.1)
            return download(*args)

        self._download = slow_download
        self.assertEqual(list(self._fetch().columns), ["AAA", "BBB"])

    def test_download_prices_uses_the_store(self):
        with mock.patch.object(market_data, "_download_prices_uncached", side_effect=self._download):
            market_data.download_prices(("AAA", "BBB"), self.start, self.end)
            market_data.clear_price_cache()
            market_data.download_prices(("AAA", "BBB"), self.start, self.end)
        self.assertEqual(len(self.downloads), 1)


if __name__ == "__main__":
    unittest.main()
