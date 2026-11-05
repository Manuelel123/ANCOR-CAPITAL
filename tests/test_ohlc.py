"""OHLCV for the price explorer: data layer, payload rules, endpoint and markup (no network)."""

from datetime import date
import os
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import requests
from requests.exceptions import HTTPError
from tiingo.restclient import RestClientError
from yfinance.exceptions import YFRateLimitError

from support import WebTestCase, create_test_user, login_session, make_prices

from optimizacion_portafolios.data.market_data import (
    OHLCV_COLUMNS,
    clear_price_cache,
    download_ohlcv,
)
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.common.price_series import ohlc_entry, price_series_payload
from optimizacion_portafolios.web.history.services import FULL_HISTORY_START

_CLIENT = "optimizacion_portafolios.data.tiingo_prices._client"
_YF_DOWNLOAD = "optimizacion_portafolios.data.market_data.yf.download"
_SERVICES = "optimizacion_portafolios.web.history.services"


def _without_tiingo_key():
    environment = {key: value for key, value in os.environ.items() if key != "TIINGO_API_KEY"}
    return patch.dict(os.environ, environment, clear=True)


def _with_tiingo_key():
    return patch.dict(os.environ, {"TIINGO_API_KEY": "test-key"})


def _yahoo_ohlcv(index, ticker, rows):
    """yfinance >= 0.2 shape: MultiIndex (Price, Ticker) even for one ticker."""
    fields = ("Open", "High", "Low", "Close", "Volume")
    columns = pd.MultiIndex.from_tuples([(field, ticker) for field in fields], names=["Price", "Ticker"])
    return pd.DataFrame(rows, index=index, columns=columns)


def _ohlc_frame(rows: int = 30, seed: int = 7) -> pd.DataFrame:
    """Plausible OHLCV with a real intraday range."""
    close = make_prices(("X",), rows=rows)["X"]
    rng = np.random.default_rng(seed)
    open_ = close.shift(1).fillna(close.iloc[0]) * (1 + rng.normal(0, 0.002, rows))
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    volume = rng.integers(1_000, 5_000, rows).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=close.index
    )


class DownloadOhlcvTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_price_cache()

    def tearDown(self) -> None:
        clear_price_cache()

    @_without_tiingo_key()
    @patch(_YF_DOWNLOAD)
    def test_yahoo_multiindex_single_ticker(self, download) -> None:
        index = pd.DatetimeIndex(["2025-01-02 00:00", "2025-01-03 00:00", "2025-01-03 00:00"], tz="America/Bogota")
        download.return_value = _yahoo_ohlcv(
            index, "^GSPC", [[1, 2, 0.5, 1.5, 10], [1.5, 3, 1, 2, 20], [1.6, 3.1, 1.1, 2.1, 21]]
        )
        frame = download_ohlcv("^GSPC", date(2025, 1, 1), date(2025, 1, 3))
        self.assertEqual(list(frame.columns), list(OHLCV_COLUMNS))
        self.assertEqual(frame.index.name, "Fecha")
        self.assertIsNone(frame.index.tz)
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame.iloc[-1].tolist(), [1.6, 3.1, 1.1, 2.1, 21.0])
        self.assertTrue(download.call_args.kwargs["auto_adjust"])
        self.assertEqual(download.call_args.args[0], ["^GSPC"])

    @_without_tiingo_key()
    @patch(_YF_DOWNLOAD)
    def test_yahoo_without_open_keeps_nan_columns_and_caches(self, download) -> None:
        index = pd.bdate_range("2025-01-01", periods=2)
        download.return_value = pd.DataFrame({"Close": [1.0, 2.0]}, index=index)
        first = download_ohlcv("FUNDX", date(2025, 1, 1), date(2025, 1, 2))
        self.assertTrue(first["open"].isna().all())
        self.assertEqual(first["close"].tolist(), [1.0, 2.0])
        first.loc[:, "close"] = 0.0
        second = download_ohlcv("FUNDX", date(2025, 1, 1), date(2025, 1, 2))
        self.assertEqual(second["close"].tolist(), [1.0, 2.0])
        download.assert_called_once()

    @_without_tiingo_key()
    @patch("optimizacion_portafolios.data.market_data.monotonic")
    @patch(_YF_DOWNLOAD, return_value=pd.DataFrame())
    def test_empty_download_is_cached_only_briefly(self, download, clock) -> None:
        clock.return_value = 500.0
        self.assertTrue(download_ohlcv("ZZZ", date(2025, 1, 1), date(2025, 1, 2)).empty)
        self.assertTrue(download_ohlcv("ZZZ", date(2025, 1, 1), date(2025, 1, 2)).empty)
        self.assertEqual(download.call_count, 1)
        clock.return_value = 500.0 + 60
        self.assertTrue(download_ohlcv("ZZZ", date(2025, 1, 1), date(2025, 1, 2)).empty)
        self.assertEqual(download.call_count, 2)

    @_with_tiingo_key()
    @patch(_CLIENT)
    @patch(_YF_DOWNLOAD)
    def test_tiingo_uses_adjusted_fields(self, download, client) -> None:
        client.return_value.get_ticker_price.return_value = [
            {"date": "2025-01-02T00:00:00.000Z", "open": 99, "adjOpen": 9.0, "adjHigh": 11.0,
             "adjLow": 8.0, "adjClose": 10.0, "adjVolume": 500},
            {"date": "2025-01-03T00:00:00.000Z", "open": 99, "adjOpen": 10.0, "adjHigh": 12.0,
             "adjLow": 9.5, "adjClose": 11.0, "adjVolume": 600},
        ]
        frame = download_ohlcv("AAPL", date(2025, 1, 2), date(2025, 1, 3))
        self.assertEqual(frame.loc["2025-01-02"].tolist(), [9.0, 11.0, 8.0, 10.0, 500.0])
        download.assert_not_called()

    @_with_tiingo_key()
    @patch(_CLIENT)
    @patch(_YF_DOWNLOAD)
    def test_tiingo_crypto_and_fallback_to_yahoo(self, download, client) -> None:
        client.return_value.get_crypto_price_history.return_value = [
            {"ticker": "btcusd", "priceData": [
                {"date": "2025-01-02T00:00:00+00:00", "open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0, "volume": 7.0}
            ]}
        ]
        frame = download_ohlcv("BTC-USD", date(2025, 1, 2), date(2025, 1, 2))
        self.assertEqual(frame.iloc[0].tolist(), [1.0, 3.0, 0.5, 2.0, 7.0])
        self.assertEqual(client.return_value.get_crypto_price_history.call_args.kwargs["tickers"], ["btcusd"])

        response = requests.Response()
        response.status_code = 404
        client.return_value.get_ticker_price.side_effect = RestClientError(HTTPError("404", response=response))
        download.return_value = _yahoo_ohlcv(pd.DatetimeIndex(["2025-01-02"]), "OLDFUND", [[1, 1, 1, 1, 0]])
        frame = download_ohlcv("OLDFUND", date(2025, 1, 2), date(2025, 1, 2))
        self.assertEqual(frame.iloc[0]["close"], 1.0)
        download.assert_called_once()

    @_with_tiingo_key()
    @patch(_CLIENT)
    @patch(_YF_DOWNLOAD)
    def test_tiingo_rate_limit_falls_back_to_yahoo(self, download, client) -> None:
        response = requests.Response()
        response.status_code = 429
        client.return_value.get_ticker_price.side_effect = RestClientError(HTTPError("429", response=response))
        download.return_value = _yahoo_ohlcv(pd.DatetimeIndex(["2025-01-02"]), "AAPL", [[1, 1, 1, 1, 0]])
        frame = download_ohlcv("AAPL", date(2025, 1, 2), date(2025, 1, 2))
        self.assertEqual(frame.iloc[0]["close"], 1.0)
        download.assert_called_once()


class OhlcEntryTests(unittest.TestCase):
    def test_full_ohlcv(self) -> None:
        frame = _ohlc_frame()
        frame.iloc[0, frame.columns.get_loc("high")] = frame["close"].iloc[0] * 0.5  # inconsistent high
        frame.iloc[5, frame.columns.get_loc("volume")] = np.nan
        entry = ohlc_entry("AAPL", frame)
        self.assertTrue(entry["ohlc"])
        self.assertIsNone(entry["ohlcReason"])
        self.assertTrue(entry["hasVolume"])
        self.assertIsNone(entry["volume"][5])
        for key in ("time", "open", "high", "low", "close", "volume"):
            self.assertEqual(len(entry[key]), 30, key)
        self.assertGreaterEqual(entry["high"][0], max(entry["open"][0], entry["close"][0]))
        for i in range(30):
            self.assertLessEqual(entry["low"][i], min(entry["open"][i], entry["close"][i]))

    def test_closes_only_disables_ohlc_without_inventing_it(self) -> None:
        frame = _ohlc_frame()
        frame[["open", "high", "low", "volume"]] = np.nan
        entry = ohlc_entry("FUND", frame)
        self.assertFalse(entry["ohlc"])
        self.assertIn("solo publica precios de cierre", entry["ohlcReason"])
        self.assertNotIn("open", entry)
        self.assertNotIn("high", entry)
        self.assertEqual(len(entry["close"]), 30)
        self.assertFalse(entry["hasVolume"])
        self.assertIsNone(entry["volume"])
        self.assertIn("volumen", entry["volumeReason"])

    def test_flat_quotes_are_not_offered_as_candles(self) -> None:
        frame = _ohlc_frame()
        frame["open"] = frame["high"] = frame["low"] = frame["close"]
        entry = ohlc_entry("VFIAX", frame)
        self.assertFalse(entry["ohlc"])
        self.assertIn("un solo valor por día", entry["ohlcReason"])

    def test_partial_coverage(self) -> None:
        frame = _ohlc_frame(rows=40)
        frame.iloc[:10, frame.columns.get_loc("open")] = np.nan
        entry = ohlc_entry("X", frame)
        self.assertFalse(entry["ohlc"])
        self.assertIn("75%", entry["ohlcReason"])
        frame = _ohlc_frame(rows=40)
        frame.iloc[:2, frame.columns.get_loc("low")] = np.nan
        entry = ohlc_entry("X", frame)
        self.assertTrue(entry["ohlc"])
        self.assertEqual(len(entry["time"]), 38)

    def test_zero_volume_counts_as_missing_and_bad_closes_are_dropped(self) -> None:
        frame = _ohlc_frame()
        frame["volume"] = 0.0
        frame.iloc[3, frame.columns.get_loc("close")] = np.inf
        entry = ohlc_entry("EURUSD=X", frame)
        self.assertFalse(entry["hasVolume"])
        self.assertEqual(len(entry["time"]), 29)
        self.assertIsNone(ohlc_entry("X", frame.assign(close=np.nan)))


class OhlcEndpointTests(WebTestCase):
    @patch(f"{_SERVICES}.download_ohlcv")
    def test_returns_entry_for_window(self, download) -> None:
        download.return_value = _ohlc_frame()
        response = self.client.get("/api/ohlc?ticker=aapl&start=2024-01-02&end=2024-02-12")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(download.call_args.args, ("AAPL", date(2024, 1, 2), date(2024, 2, 12)))
        data = response.get_json()
        self.assertEqual(data["ticker"], "AAPL")
        self.assertTrue(data["ohlc"])
        self.assertEqual(len(data["open"]), len(data["time"]))
        self.assertIn("max-age", response.headers["Cache-Control"])

    @patch(f"{_SERVICES}.download_ohlcv")
    def test_defaults_to_full_history(self, download) -> None:
        download.return_value = _ohlc_frame()
        self.client.get("/api/ohlc?ticker=SPY")
        ticker, start, end = download.call_args.args
        self.assertEqual((ticker, start), ("SPY", FULL_HISTORY_START))
        self.assertEqual(end, date.today())

    def test_invalid_parameters(self) -> None:
        with patch(f"{_SERVICES}.download_ohlcv") as download:
            for query in (
                {"ticker": "bad ticker"},
                {"ticker": "AAPL", "start": "2024-13-01"},
                {"ticker": "AAPL", "start": "2024-02-01", "end": "2024-01-01"},
            ):
                with self.subTest(query=query):
                    response = self.client.get("/api/ohlc", query_string=query)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.get_json())
            download.assert_not_called()

    @patch(f"{_SERVICES}.download_ohlcv", return_value=pd.DataFrame())
    def test_no_data_is_not_found(self, _download) -> None:
        response = self.client.get("/api/ohlc?ticker=ZZZZQQ")
        self.assertEqual(response.status_code, 404)
        self.assertIn("ZZZZQQ", response.get_json()["error"])

    @patch(f"{_SERVICES}.download_ohlcv", side_effect=YFRateLimitError())
    def test_rate_limit(self, _download) -> None:
        self.assertEqual(self.client.get("/api/ohlc?ticker=AAPL").status_code, 503)

    @patch(f"{_SERVICES}.download_ohlcv", side_effect=requests.ConnectionError("sin red"))
    def test_provider_failure(self, _download) -> None:
        response = self.client.get("/api/ohlc?ticker=AAPL")
        self.assertEqual(response.status_code, 502)
        self.assertIn("OHLC", response.get_json()["error"])


class ExplorerControlsMarkupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.macro = self.app.jinja_env.get_template("macros/charts.html").module.price_explorer
        self.payload = price_series_payload(make_prices(("AAPL",), rows=40))

    def test_chart_types_and_indicator_panel(self) -> None:
        with self.app.test_request_context("/"):
            html = str(self.macro("main", self.payload, history_url="/api/historial"))
        self.assertIn('data-ohlc-url="/api/ohlc"', html)
        self.assertIn("data-chart-type", html)
        for label in ("Área", "Línea", "Línea base", "Histograma", "Barras OHLC", "Velas"):
            self.assertIn(f">{label}</option>", html)
        self.assertEqual(html.count("data-needs-ohlc"), 2)
        self.assertIn('aria-controls="prices-main-indicators"', html)
        self.assertIn('id="prices-main-indicators" data-indicators-panel hidden', html)
        self.assertIn("data-indicator-add", html)

    def test_without_history_url_there_is_no_ohlc_source(self) -> None:
        html = str(self.macro("main", self.payload))
        self.assertNotIn("data-ohlc-url", html)
        self.assertIn("data-chart-type", html)
        html = str(self.macro("main", self.payload, ohlc_url="/custom/ohlc"))
        self.assertIn('data-ohlc-url="/custom/ohlc"', html)

    def test_pages_load_the_indicator_module(self) -> None:
        client = self.app.test_client()
        login_session(client, create_test_user(self.app))
        html = client.get("/hrp").data.decode()
        self.assertIn("js/indicators.js", html)
        self.assertLess(html.index("js/indicators.js"), html.index("js/charts.js"))


if __name__ == "__main__":
    unittest.main()
