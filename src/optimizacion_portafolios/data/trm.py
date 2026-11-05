"""Daily dollar rate in Colombian pesos.

The official TRM comes from the open data portal of the Colombian government;
Yahoo Finance is the fallback, then the last rate seen. Rates are cached, and a
failure is not retried for a few minutes, so a page never waits on every load.
``current_rate`` returns ``None`` when no source has answered yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from time import monotonic

import requests

from optimizacion_portafolios.data import market_data

logger = logging.getLogger(__name__)

TRM_URL = "https://www.datos.gov.co/resource/32sa-8pi3.json"
TRM_SOURCE = "TRM"
YAHOO_SOURCE = "Yahoo Finance"
_TIMEOUT_SECONDS = 5
_CACHE_SECONDS = 6 * 60 * 60
_RETRY_SECONDS = 5 * 60


@dataclass(frozen=True)
class DollarRate:
    value: float  # pesos per dollar
    day: str  # ISO date
    source: str


_last_rate: DollarRate | None = None
_next_refresh = 0.0


def _fetch_trm() -> DollarRate:
    response = requests.get(
        TRM_URL, params={"$limit": 1, "$order": "vigenciadesde DESC"}, timeout=_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    row = response.json()[0]
    return DollarRate(float(row["valor"]), row["vigenciadesde"][:10], TRM_SOURCE)


def _fetch_yahoo() -> DollarRate:
    return DollarRate(1 / market_data.exchange_rate_to_usd("COP"), date.today().isoformat(), YAHOO_SOURCE)


def current_rate() -> DollarRate | None:
    """The latest known rate, refreshed at most every few hours."""
    global _last_rate, _next_refresh
    if monotonic() < _next_refresh:
        return _last_rate
    for fetch in (_fetch_trm, _fetch_yahoo):
        try:
            rate = fetch()
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as error:
            logger.warning("Dollar rate from %s failed: %s", fetch.__name__, type(error).__name__)
            continue
        if rate.value > 0:
            _last_rate = rate
            _next_refresh = monotonic() + _CACHE_SECONDS
            return rate
    _next_refresh = monotonic() + _RETRY_SECONDS
    return _last_rate


def clear_rate() -> None:
    """Forget the rate (used by tests)."""
    global _last_rate, _next_refresh
    _last_rate, _next_refresh = None, 0.0
