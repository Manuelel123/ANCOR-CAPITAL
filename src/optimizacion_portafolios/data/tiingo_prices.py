"""Tiingo price downloads and routing between Tiingo and Yahoo Finance.

Tiingo covers US equities, ETFs and mutual funds through its daily endpoint and
crypto pairs through its crypto endpoint. Colombian shares (``.CL``), other
non-US listings (any ``.`` suffix), futures (``=F``) and indices (``^``) are not
available there, so they are routed to Yahoo Finance.
"""

from datetime import date, timedelta
from enum import Enum
from functools import cache
import logging
import os
import re

import pandas as pd
import requests
from tiingo import TiingoClient
from tiingo.restclient import RestClientError

logger = logging.getLogger(__name__)
API_KEY_ENV_VAR = "TIINGO_API_KEY"
_TIMEOUT_SECONDS = 20
_CRYPTO_PATTERN = re.compile(r"^([A-Z0-9]+)-USD$")
_NOT_FOUND = 404
_RATE_LIMITED = 429


class PriceSource(Enum):
    TIINGO = "tiingo"
    TIINGO_CRYPTO = "tiingo_crypto"
    YFINANCE = "yfinance"


class _TimeoutTiingoClient(TiingoClient):
    """Tiingo client whose HTTP requests always carry a timeout."""

    def _request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", _TIMEOUT_SECONDS)
        return super()._request(method, url, **kwargs)


def price_source(ticker: str) -> PriceSource:
    """Decide which provider should serve the price history of a ticker."""
    symbol = ticker.strip().upper()
    if not symbol or symbol.startswith("^") or "=" in symbol or "." in symbol:
        return PriceSource.YFINANCE
    if _CRYPTO_PATTERN.match(symbol):
        return PriceSource.TIINGO_CRYPTO
    return PriceSource.TIINGO


def to_tiingo_crypto_ticker(ticker: str) -> str:
    """Convert a Yahoo crypto pair such as ``BTC-USD`` into ``btcusd``."""
    match = _CRYPTO_PATTERN.match(ticker.strip().upper())
    if match is None:
        raise ValueError(f"El ticker {ticker} no es un par de criptomoneda en USD.")
    return f"{match.group(1)}usd".lower()


def tiingo_api_key() -> str | None:
    """Return the Tiingo API key, warning once when it is not configured."""
    api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if api_key:
        return api_key
    _warn_missing_api_key()
    return None


@cache
def _warn_missing_api_key() -> None:
    logger.warning(
        "%s is not set; downloading every price from Yahoo Finance.",
        API_KEY_ENV_VAR,
    )


def _client(api_key: str) -> TiingoClient:
    return _TimeoutTiingoClient({"api_key": api_key, "session": True})


def normalize_dates(index: pd.Index) -> pd.DatetimeIndex:
    """Return tz-naive dates without time so every provider aligns by day."""
    dates = pd.DatetimeIndex(pd.to_datetime(index))
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    return dates.normalize()


def _status_code(error: RestClientError) -> int | None:
    cause = error.args[0] if error.args else None
    response = getattr(cause, "response", None)
    return getattr(response, "status_code", None)


def _falls_back_to_yahoo(error: RestClientError) -> bool:
    """True when Yahoo should serve the data: not covered, or allowance used up."""
    status = _status_code(error)
    if status == _RATE_LIMITED:
        logger.warning("Tiingo request allowance used up; falling back to Yahoo Finance.")
    return status in (_NOT_FOUND, _RATE_LIMITED)


def _http_error(error: RestClientError, description: str) -> requests.HTTPError:
    status = _status_code(error)
    cause = error.args[0] if error.args else None
    return requests.HTTPError(
        f"Tiingo respondió con un error (HTTP {status or 'desconocido'}) "
        f"al descargar {description}.",
        response=getattr(cause, "response", None),
    )


def _series(records: list[dict], field: str, name: str) -> pd.Series | None:
    rows = [
        (record["date"], record[field])
        for record in records
        if record.get("date") and record.get(field) is not None
    ]
    if not rows:
        return None
    dates, values = zip(*rows)
    series = pd.Series(
        values, index=normalize_dates(list(dates)), name=name, dtype=float
    )
    series = series[~series.index.duplicated(keep="last")]
    return series.sort_index()


def download_tiingo_prices(
    tickers: tuple[str, ...], start: date, end: date, api_key: str
) -> dict[str, pd.Series]:
    """Download adjusted closes of stock/ETF tickers; unknown tickers are omitted.

    A 404 response means Tiingo does not cover the ticker and is not an error;
    a 429 means the allowance is used up, so the remaining tickers are left out
    too (Yahoo serves them). Any other HTTP failure is raised as
    ``requests.HTTPError``.
    """
    client = _client(api_key)
    prices: dict[str, pd.Series] = {}
    for ticker in tickers:
        try:
            records = client.get_ticker_price(
                ticker,
                startDate=start.isoformat(),
                endDate=end.isoformat(),
                fmt="json",
                frequency="daily",
            )
        except RestClientError as error:
            if not _falls_back_to_yahoo(error):
                raise _http_error(error, ticker) from error
            if _status_code(error) == _RATE_LIMITED:
                break
            continue
        series = _series(records or [], "adjClose", ticker)
        if series is not None:
            prices[ticker] = series
    return prices


def download_tiingo_crypto_prices(
    tickers: tuple[str, ...], start: date, end: date, api_key: str
) -> dict[str, pd.Series]:
    """Download daily closes of ``XXX-USD`` pairs from Tiingo's crypto endpoint.

    Crypto has no splits or dividends, so the close is already the adjusted
    close. Pairs Tiingo does not return are omitted, and so are all of them
    when the allowance is used up.
    """
    if not tickers:
        return {}
    by_tiingo_ticker = {to_tiingo_crypto_ticker(ticker): ticker for ticker in tickers}
    try:
        response = _client(api_key).get_crypto_price_history(
            tickers=list(by_tiingo_ticker),
            startDate=start.isoformat(),
            endDate=(end + timedelta(days=1)).isoformat(),
            resampleFreq="1day",
        )
    except RestClientError as error:
        if _falls_back_to_yahoo(error):
            return {}
        raise _http_error(error, ", ".join(tickers)) from error

    prices: dict[str, pd.Series] = {}
    for item in response or []:
        ticker = by_tiingo_ticker.get(str(item.get("ticker", "")).lower())
        if ticker is None:
            continue
        series = _series(item.get("priceData") or [], "close", ticker)
        if series is not None:
            prices[ticker] = series
    return prices


# Daily OHLCV fields: adjusted for stocks/ETFs (same basis as ``adjClose``),
# raw for crypto (no corporate actions). Keys are the output column names.
_STOCK_OHLCV_FIELDS = {
    "open": "adjOpen",
    "high": "adjHigh",
    "low": "adjLow",
    "close": "adjClose",
    "volume": "adjVolume",
}
_CRYPTO_OHLCV_FIELDS = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}


def _ohlcv_frame(records: list[dict], fields: dict[str, str]) -> pd.DataFrame | None:
    """OHLCV frame (columns open/high/low/close/volume) or None without closes."""
    columns = {
        column: _series(records, field, column) for column, field in fields.items()
    }
    if columns["close"] is None:
        return None
    frame = pd.concat(
        [
            series if series is not None else pd.Series(dtype=float, name=column)
            for column, series in columns.items()
        ],
        axis=1,
    )
    return frame.loc[frame["close"].notna()].sort_index()


def download_tiingo_ohlcv(
    ticker: str, start: date, end: date, api_key: str
) -> pd.DataFrame | None:
    """Daily adjusted OHLCV of a stock/ETF/fund, or None when Tiingo lacks it."""
    try:
        records = _client(api_key).get_ticker_price(
            ticker,
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            fmt="json",
            frequency="daily",
        )
    except RestClientError as error:
        if _falls_back_to_yahoo(error):
            return None
        raise _http_error(error, ticker) from error
    return _ohlcv_frame(records or [], _STOCK_OHLCV_FIELDS)


def download_tiingo_crypto_ohlcv(
    ticker: str, start: date, end: date, api_key: str
) -> pd.DataFrame | None:
    """Daily OHLCV of an ``XXX-USD`` pair, or None when Tiingo lacks it."""
    tiingo_ticker = to_tiingo_crypto_ticker(ticker)
    try:
        response = _client(api_key).get_crypto_price_history(
            tickers=[tiingo_ticker],
            startDate=start.isoformat(),
            endDate=(end + timedelta(days=1)).isoformat(),
            resampleFreq="1day",
        )
    except RestClientError as error:
        if _falls_back_to_yahoo(error):
            return None
        raise _http_error(error, ticker) from error
    for item in response or []:
        if str(item.get("ticker", "")).lower() == tiingo_ticker:
            return _ohlcv_frame(item.get("priceData") or [], _CRYPTO_OHLCV_FIELDS)
    return None
