"""Tests for the market data-access layer (network is always mocked)."""

from datetime import date
import os
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import requests
from requests.exceptions import HTTPError
from tiingo import TiingoClient
from tiingo.restclient import RestClientError
from yfinance.exceptions import YFRateLimitError

from optimizacion_portafolios.data import market_data, tiingo_prices
from optimizacion_portafolios.data.market_data import (
    _cached_ticker_info,
    clear_price_cache,
    download_prices,
    fetch_fundamental_information,
    fetch_market_cap_usd,
)
from optimizacion_portafolios.data.tiingo_prices import (
    PriceSource,
    price_source,
    to_tiingo_crypto_ticker,
)

_CLIENT = "optimizacion_portafolios.data.tiingo_prices._client"
_YF_DOWNLOAD = "optimizacion_portafolios.data.market_data.yf.download"


def _without_tiingo_key():
    environment = {
        key: value for key, value in os.environ.items() if key != "TIINGO_API_KEY"
    }
    return patch.dict(os.environ, environment, clear=True)


def _with_tiingo_key():
    return patch.dict(os.environ, {"TIINGO_API_KEY": "test-key"})


def _tiingo_records(days, values, field="adjClose"):
    return [
        {"date": f"{day}T00:00:00.000Z", "close": value * 2, field: value}
        for day, value in zip(days, values)
    ]


def _tiingo_http_error(status=404):
    response = requests.Response()
    response.status_code = status
    return RestClientError(HTTPError(f"{status} Error", response=response))


def _yahoo_close(index, prices_by_ticker):
    columns = pd.MultiIndex.from_product([["Close"], list(prices_by_ticker)])
    values = np.column_stack(list(prices_by_ticker.values()))
    return pd.DataFrame(values, index=index, columns=columns)


class MarketDataTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_price_cache()

    def tearDown(self) -> None:
        _cached_ticker_info.cache_clear()
        clear_price_cache()

    @_without_tiingo_key()
    @patch(_YF_DOWNLOAD)
    def test_download_prices_selects_close_level_from_multiindex(self, download):
        index = pd.bdate_range("2025-01-01", periods=3, name="Date")
        columns = pd.MultiIndex.from_product(
            [["Close", "Open"], ["AAPL", "MSFT"]]
        )
        values = np.arange(12, dtype=float).reshape(3, 4)
        download.return_value = pd.DataFrame(values, index=index, columns=columns)

        result = download_prices(("MSFT", "AAPL"), index[0].date(), index[-1].date())

        self.assertEqual(list(result.columns), ["MSFT", "AAPL"])
        self.assertEqual(result.index.name, "Fecha")
        self.assertEqual(result.iloc[0].tolist(), [1.0, 0.0])
        self.assertFalse(download.call_args.kwargs["threads"])

    @_without_tiingo_key()
    @patch(_CLIENT)
    @patch(_YF_DOWNLOAD)
    def test_without_api_key_uses_yahoo_and_warns_once(self, download, client):
        tiingo_prices._warn_missing_api_key.cache_clear()
        index = pd.bdate_range("2025-01-01", periods=2)
        download.return_value = _yahoo_close(index, {"AAPL": [1.0, 2.0]})

        with self.assertLogs(tiingo_prices.logger, "WARNING") as logs:
            download_prices(("AAPL",), date(2025, 1, 1), date(2025, 1, 2))
            download_prices(("AAPL",), date(2025, 1, 1), date(2025, 1, 2))

        self.assertEqual(len(logs.records), 1)
        self.assertIn("TIINGO_API_KEY", logs.output[0])
        client.assert_not_called()
        self.assertEqual(download.call_args.args[0], ["AAPL"])

    @_without_tiingo_key()
    @patch(_YF_DOWNLOAD)
    def test_download_prices_cache_avoids_second_download(self, download):
        index = pd.bdate_range("2025-01-01", periods=2)
        download.return_value = _yahoo_close(index, {"AAPL": [1.0, 2.0]})
        start, end = date(2025, 1, 1), date(2025, 1, 2)

        first = download_prices(("AAPL",), start, end)
        second = download_prices(("AAPL",), start, end)

        self.assertEqual(download.call_count, 1)
        pd.testing.assert_frame_equal(first, second)
        download_prices(("AAPL",), start, date(2025, 1, 3))
        self.assertEqual(download.call_count, 2)

    @_without_tiingo_key()
    @patch(_YF_DOWNLOAD)
    def test_download_prices_cache_returns_independent_copies(self, download):
        index = pd.bdate_range("2025-01-01", periods=2)
        download.return_value = _yahoo_close(index, {"AAPL": [1.0, 2.0]})
        start, end = date(2025, 1, 1), date(2025, 1, 2)

        first = download_prices(("AAPL",), start, end)
        first.iloc[0, 0] = -99.0
        first["EXTRA"] = 0.0
        second = download_prices(("AAPL",), start, end)
        second.iloc[1, 0] = -77.0
        third = download_prices(("AAPL",), start, end)

        self.assertEqual(download.call_count, 1)
        self.assertEqual(list(third.columns), ["AAPL"])
        self.assertEqual(third["AAPL"].tolist(), [1.0, 2.0])

    @_without_tiingo_key()
    @patch("optimizacion_portafolios.data.market_data.monotonic")
    @patch(_YF_DOWNLOAD)
    def test_download_prices_cache_expires_with_window(self, download, clock):
        index = pd.bdate_range("2025-01-01", periods=2)
        download.return_value = _yahoo_close(index, {"AAPL": [1.0, 2.0]})
        start, end = date(2025, 1, 1), date(2025, 1, 2)

        clock.return_value = 10.0
        download_prices(("AAPL",), start, end)
        clock.return_value = 10.0 + 14 * 60
        download_prices(("AAPL",), start, end)
        self.assertEqual(download.call_count, 1)

        clock.return_value = 10.0 + 15 * 60
        download_prices(("AAPL",), start, end)
        self.assertEqual(download.call_count, 2)

    @_without_tiingo_key()
    @patch("optimizacion_portafolios.data.market_data.monotonic")
    @patch(_YF_DOWNLOAD)
    def test_download_prices_caches_empty_results_briefly(self, download, clock):
        download.return_value = pd.DataFrame()
        start, end = date(2025, 1, 1), date(2025, 1, 2)

        clock.return_value = 100.0
        self.assertTrue(download_prices(("AAPL",), start, end).empty)
        clock.return_value = 100.0 + 59
        self.assertTrue(download_prices(("AAPL",), start, end).empty)
        self.assertEqual(download.call_count, 1)

        clock.return_value = 100.0 + 60
        self.assertTrue(download_prices(("AAPL",), start, end).empty)
        self.assertEqual(download.call_count, 2)

    @_without_tiingo_key()
    @patch("optimizacion_portafolios.data.market_data.monotonic")
    @patch(_YF_DOWNLOAD)
    def test_download_prices_cache_ttl_counts_from_the_download(self, download, clock):
        """Per-entry TTL: a download made late in a 15-minute bucket is not dropped at its end."""
        index = pd.bdate_range("2025-01-01", periods=2)
        download.return_value = _yahoo_close(index, {"AAPL": [1.0, 2.0]})
        start, end = date(2025, 1, 1), date(2025, 1, 2)

        clock.return_value = 15 * 60 - 1.0
        download_prices(("AAPL",), start, end)
        clock.return_value = 15 * 60 + 60.0
        download_prices(("AAPL",), start, end)
        self.assertEqual(download.call_count, 1)

    @patch("optimizacion_portafolios.data.market_data.yf.Ticker")
    def test_exchange_rate_is_cached(self, ticker):
        ticker.return_value.history.return_value = pd.DataFrame({"Close": [0.25, 0.26]})
        market_data._latest_exchange_rate.cache_clear()
        self.addCleanup(market_data._latest_exchange_rate.cache_clear)

        self.assertAlmostEqual(market_data._exchange_rate_to_usd("brl"), 0.26)
        self.assertAlmostEqual(market_data._exchange_rate_to_usd("BRL"), 0.26)
        self.assertEqual(ticker.return_value.history.call_count, 1)

    def test_price_source_routes_unsupported_tickers_to_yahoo(self):
        expected = {
            "AAPL": PriceSource.TIINGO,
            "BRK-B": PriceSource.TIINGO,
            "XLK": PriceSource.TIINGO,
            "BTC-USD": PriceSource.TIINGO_CRYPTO,
            "xrp-usd": PriceSource.TIINGO_CRYPTO,
            "ECOPETROL.CL": PriceSource.YFINANCE,
            "VOD.L": PriceSource.YFINANCE,
            "GC=F": PriceSource.YFINANCE,
            "EURUSD=X": PriceSource.YFINANCE,
            "^GSPC": PriceSource.YFINANCE,
            "^COLCAP": PriceSource.YFINANCE,
        }
        for ticker, source in expected.items():
            with self.subTest(ticker=ticker):
                self.assertIs(price_source(ticker), source)
        self.assertEqual(to_tiingo_crypto_ticker("BTC-USD"), "btcusd")
        with self.assertRaises(ValueError):
            to_tiingo_crypto_ticker("AAPL")

    @_with_tiingo_key()
    @patch(_CLIENT)
    @patch(_YF_DOWNLOAD)
    def test_mixed_sources_align_by_date_and_keep_requested_order(
        self, download, client
    ):
        client.return_value.get_ticker_price.return_value = _tiingo_records(
            ["2025-01-02", "2025-01-03"], [10.0, 11.0]
        )
        client.return_value.get_crypto_price_history.return_value = [
            {
                "ticker": "btcusd",
                "priceData": _tiingo_records(
                    ["2025-01-02", "2025-01-03", "2025-01-04"],
                    [100.0, 101.0, 102.0],
                    field="close",
                ),
            }
        ]
        yahoo_index = pd.DatetimeIndex(
            ["2025-01-02 00:00", "2025-01-03 00:00"], tz="America/Bogota"
        )
        download.return_value = _yahoo_close(
            yahoo_index, {"GC=F": [7.0, 8.0], "ECOPETROL.CL": [5.0, 6.0]}
        )

        result = download_prices(
            ("GC=F", "AAPL", "BTC-USD", "ECOPETROL.CL"),
            date(2025, 1, 2),
            date(2025, 1, 4),
        )

        self.assertEqual(
            list(result.columns), ["GC=F", "AAPL", "BTC-USD", "ECOPETROL.CL"]
        )
        self.assertEqual(result.index.name, "Fecha")
        self.assertIsNone(result.index.tz)
        self.assertEqual(
            list(result.index),
            list(pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-04"])),
        )
        self.assertEqual(result.loc["2025-01-03"].tolist(), [8.0, 11.0, 101.0, 6.0])
        self.assertTrue(np.isnan(result.loc["2025-01-04", "AAPL"]))
        self.assertEqual(download.call_args.args[0], ["GC=F", "ECOPETROL.CL"])
        client.return_value.get_ticker_price.assert_called_once()
        self.assertEqual(
            client.return_value.get_ticker_price.call_args.args[0], "AAPL"
        )
        crypto_call = client.return_value.get_crypto_price_history.call_args.kwargs
        self.assertEqual(crypto_call["tickers"], ["btcusd"])
        self.assertEqual(crypto_call["resampleFreq"], "1day")

    @_with_tiingo_key()
    @patch(_CLIENT)
    @patch(_YF_DOWNLOAD)
    def test_tickers_missing_in_tiingo_fall_back_to_yahoo(self, download, client):
        def ticker_price(ticker, **_kwargs):
            if ticker in {"UNKNOWN", "MISSING"}:
                raise _tiingo_http_error(404)
            if ticker == "EMPTY":
                return []
            return _tiingo_records(["2025-01-02"], [10.0])

        client.return_value.get_ticker_price.side_effect = ticker_price
        client.return_value.get_crypto_price_history.return_value = []
        download.return_value = _yahoo_close(
            pd.DatetimeIndex(["2025-01-02"]),
            {"UNKNOWN": [1.0], "EMPTY": [2.0], "BNB-USD": [3.0]},
        )

        result = download_prices(
            ("UNKNOWN", "MSFT", "EMPTY", "BNB-USD", "MISSING"),
            date(2025, 1, 2),
            date(2025, 1, 2),
        )

        self.assertEqual(
            download.call_args.args[0], ["UNKNOWN", "EMPTY", "BNB-USD", "MISSING"]
        )
        self.assertEqual(
            list(result.columns), ["UNKNOWN", "MSFT", "EMPTY", "BNB-USD"]
        )
        self.assertEqual(result.iloc[0].tolist(), [1.0, 10.0, 2.0, 3.0])

    @_with_tiingo_key()
    @patch(_CLIENT)
    @patch(_YF_DOWNLOAD)
    def test_tiingo_http_errors_are_raised_as_request_exceptions(
        self, download, client
    ):
        client.return_value.get_ticker_price.side_effect = _tiingo_http_error(500)

        with self.assertRaises(requests.RequestException) as raised:
            download_prices(("AAPL",), date(2025, 1, 2), date(2025, 1, 3))

        self.assertIn("Tiingo", str(raised.exception))
        self.assertIn("500", str(raised.exception))
        download.assert_not_called()

    @_with_tiingo_key()
    @patch(_CLIENT)
    @patch(_YF_DOWNLOAD)
    def test_tiingo_rate_limit_sends_remaining_tickers_to_yahoo(self, download, client):
        def ticker_price(ticker, **_kwargs):
            if ticker == "MSFT":
                raise _tiingo_http_error(429)
            return _tiingo_records(["2025-01-02"], [10.0])

        client.return_value.get_ticker_price.side_effect = ticker_price
        client.return_value.get_crypto_price_history.side_effect = _tiingo_http_error(429)
        download.return_value = _yahoo_close(
            pd.DatetimeIndex(["2025-01-02"]),
            {"MSFT": [1.0], "NVDA": [2.0], "BTC-USD": [3.0]},
        )

        result = download_prices(
            ("AAPL", "MSFT", "NVDA", "BTC-USD"), date(2025, 1, 2), date(2025, 1, 3)
        )

        self.assertEqual(list(result.iloc[0]), [10.0, 1.0, 2.0, 3.0])
        self.assertEqual(client.return_value.get_ticker_price.call_count, 2)
        self.assertCountEqual(download.call_args.args[0], ["MSFT", "NVDA", "BTC-USD"])

    @_without_tiingo_key()
    @patch(_YF_DOWNLOAD)
    def test_days_without_any_close_are_dropped(self, download):
        download.return_value = _yahoo_close(
            pd.DatetimeIndex(["2025-01-02", "2025-01-03", "2025-01-06"]),
            {"AAPL": [1.0, 2.0, np.nan], "MSFT": [3.0, np.nan, np.nan]},
        )

        result = download_prices(("AAPL", "MSFT"), date(2025, 1, 2), date(2025, 1, 6))

        self.assertEqual(list(result.index), list(pd.DatetimeIndex(["2025-01-02", "2025-01-03"])))
        self.assertEqual(result.loc["2025-01-03", "AAPL"], 2.0)
        self.assertTrue(np.isnan(result.loc["2025-01-03", "MSFT"]))

    @patch.object(TiingoClient, "_request")
    def test_tiingo_client_requests_use_timeout(self, request):
        tiingo_prices._client("test-key").get_ticker_price("AAPL")

        self.assertEqual(request.call_args.kwargs["timeout"], 20)

    @patch("optimizacion_portafolios.data.market_data.yf.Ticker")
    def test_fundamental_and_market_cap_share_cached_ticker_info(self, ticker):
        ticker.return_value.get_info.return_value = {
            "longName": "Example Corp.",
            "currency": "USD",
            "marketCap": 1_000_000_000,
        }

        profile = fetch_fundamental_information("CACHE-TEST")
        profile["longName"] = "Modified locally"
        market_cap = fetch_market_cap_usd("CACHE-TEST")

        self.assertEqual(market_cap, 1_000_000_000)
        self.assertEqual(ticker.return_value.get_info.call_count, 1)
        self.assertEqual(
            fetch_fundamental_information("CACHE-TEST")["longName"],
            "Example Corp.",
        )

    @patch("optimizacion_portafolios.data.market_data.sleep")
    @patch("optimizacion_portafolios.data.market_data.yf.Ticker")
    def test_ticker_info_retries_yahoo_rate_limit(self, ticker, sleep_mock):
        ticker.return_value.get_info.side_effect = [
            YFRateLimitError(),
            {"longName": "Recovered Corp."},
        ]

        self.assertEqual(
            fetch_fundamental_information("RETRY-TEST"),
            {"longName": "Recovered Corp."},
        )
        self.assertEqual(ticker.return_value.get_info.call_count, 2)
        sleep_mock.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
