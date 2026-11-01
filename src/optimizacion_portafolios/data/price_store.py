"""Persistent adjusted-close cache in InsForge, refreshed incrementally.

Sits below the in-memory cache of ``market_data.download_prices``. For every
requested ticker, ``fetch`` picks the cheapest safe source:

* **Stored** - the stored coverage includes the range and is fresh. A range
  ending before today never goes stale (past closes do not change except by
  adjustments, handled below); a range reaching today must have been updated
  within ``intraday_ttl`` (today's bar may still be intraday).
* **Tail** - only the recent days are missing or stale: download from a few
  days before the stored end (``OVERLAP_DAYS``) to the requested end. The
  overlap is compared with the stored closes; a difference means a dividend or
  split re-adjusted the history, so the ticker falls back to a full download.
* **Full** - no coverage, history earlier than the stored start is needed, or
  the last full download is older than ``full_refresh`` (7 days by default):
  download the whole range again.

The tables are server-only: every call uses the project API key. The store is
optional; without configuration, or on any InsForge failure, ``fetch`` returns
``None`` and the caller downloads everything directly. The InsForge calls of
one ``fetch`` share a time budget (provider downloads do not count): a store
slower than the budget is a failure too, since a direct download is faster.
After a failure the store is skipped for ``FAILURE_PAUSE_SECONDS``.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import pandas as pd

from optimizacion_portafolios.insforge import InsForgeClient, InsForgeError, in_filter

logger = logging.getLogger(__name__)

DEFAULT_FULL_REFRESH_DAYS = 7.0
DEFAULT_INTRADAY_TTL_MINUTES = 60.0
DEFAULT_BUDGET_SECONDS = 10.0
FAILURE_PAUSE_SECONDS = 600.0
# Calendar days re-downloaded before the stored end to detect re-adjustments.
OVERLAP_DAYS = 7
# Relative difference between a stored and a fresh close that signals a
# re-adjusted history (dividends are usually >= 0.1 %; float noise is ~1e-9).
ADJUSTMENT_TOLERANCE = 1e-4
_WRITE_CHUNK = 1000

Downloader = Callable[[tuple[str, ...], date, date], pd.DataFrame]

_client: InsForgeClient | None = None
_full_refresh = timedelta(days=DEFAULT_FULL_REFRESH_DAYS)
_intraday_ttl = timedelta(minutes=DEFAULT_INTRADAY_TTL_MINUTES)
_budget_seconds = DEFAULT_BUDGET_SECONDS
# ``time.monotonic()`` until which the store is skipped after a failure.
_paused_until = 0.0


def configure(
    client: InsForgeClient | None,
    full_refresh_days: float = DEFAULT_FULL_REFRESH_DAYS,
    intraday_ttl_minutes: float = DEFAULT_INTRADAY_TTL_MINUTES,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
) -> None:
    """Enable the store with a client holding the API key (``None`` disables it)."""
    global _client, _full_refresh, _intraday_ttl, _budget_seconds, _paused_until
    _client = client if client is not None and client.has_admin_key else None
    _full_refresh = timedelta(days=full_refresh_days)
    _intraday_ttl = timedelta(minutes=intraday_ttl_minutes)
    _budget_seconds = budget_seconds
    _paused_until = 0.0


def enabled() -> bool:
    return _client is not None


class _Deadline:
    """Monotonic deadline for the InsForge calls of one ``fetch``."""

    def __init__(self, seconds: float):
        self.at = time.monotonic() + seconds

    @contextmanager
    def paused(self):
        """Time spent inside (provider downloads) does not use up the budget."""
        started = time.monotonic()
        try:
            yield
        finally:
            self.at += time.monotonic() - started


@dataclass(frozen=True)
class _Coverage:
    start: date
    end: date
    fetched_at: datetime
    full_refreshed_at: datetime


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _coverage(tickers: tuple[str, ...], deadline: _Deadline) -> dict[str, _Coverage]:
    rows = _client.select(
        "price_coverage",
        {
            "ticker": in_filter(tickers),
            "select": "ticker,start_date,end_date,fetched_at,full_refreshed_at",
        },
        admin=True,
        deadline=deadline.at,
    )
    return {
        row["ticker"]: _Coverage(
            date.fromisoformat(row["start_date"][:10]),
            date.fromisoformat(row["end_date"][:10]),
            _timestamp(row["fetched_at"]),
            _timestamp(row.get("full_refreshed_at") or row["fetched_at"]),
        )
        for row in rows
    }


def _stored_series(tickers, start: date, end: date, deadline: _Deadline) -> dict[str, pd.Series]:
    if not tickers:
        return {}
    rows = _client.select(
        "price_history",
        {
            "ticker": in_filter(tickers),
            "and": f"(day.gte.{start.isoformat()},day.lte.{end.isoformat()})",
            "select": "ticker,day,close",
            "order": "ticker.asc,day.asc",
        },
        admin=True,
        deadline=deadline.at,
    )
    if not rows:
        return {}
    frame = pd.DataFrame(rows)
    frame["day"] = pd.to_datetime(frame["day"].str[:10])
    return {
        str(ticker): pd.Series(
            group["close"].astype(float).to_numpy(),
            index=pd.DatetimeIndex(group["day"], name="Fecha"),
            name=str(ticker),
        )
        for ticker, group in frame.groupby("ticker", sort=False)
    }


def _download_groups(
    plans: dict[str, tuple[date, date]], download: Downloader, deadline: _Deadline
) -> dict[str, pd.Series]:
    """Download each ``ticker -> (start, end)`` plan, batching equal ranges."""
    groups: dict[tuple[date, date], list[str]] = defaultdict(list)
    for ticker, window in plans.items():
        groups[window].append(ticker)
    series: dict[str, pd.Series] = {}
    for (start, end), tickers in groups.items():
        with deadline.paused():
            frame = download(tuple(tickers), start, end)
        for ticker in frame.columns:
            values = frame[ticker].dropna()
            values = values[values > 0]
            if not values.empty:
                series[str(ticker)] = values
    return series


def _readjusted(stored: pd.Series | None, fresh: pd.Series) -> bool:
    """True when the overlap shows the stored history was re-adjusted.

    The last stored day is skipped: it may hold an intraday close.
    """
    if stored is None or len(stored) < 2:
        return False
    stored = stored.iloc[:-1]
    common = stored.index.intersection(fresh.index)
    if common.empty:
        return False
    old, new = stored.loc[common], fresh.loc[common]
    return bool(((old - new).abs() / new.abs() > ADJUSTMENT_TOLERANCE).any())


def _write(series: dict[str, pd.Series], deadline: _Deadline) -> None:
    rows = [
        {"ticker": ticker, "day": day.date().isoformat(), "close": float(value)}
        for ticker, values in series.items()
        for day, value in values.items()
    ]
    for offset in range(0, len(rows), _WRITE_CHUNK):
        _client.insert(
            "price_history",
            rows[offset : offset + _WRITE_CHUNK],
            admin=True,
            upsert=True,
            on_conflict="ticker,day",
            returning=False,
            deadline=deadline.at,
        )


def _write_coverage(rows: list[dict], deadline: _Deadline) -> None:
    if rows:
        _client.insert(
            "price_coverage", rows, admin=True, upsert=True,
            on_conflict="ticker", returning=False, deadline=deadline.at,
        )


def fetch(tickers: tuple[str, ...], start: date, end: date, download: Downloader) -> pd.DataFrame | None:
    """Closes of ``tickers`` on ``[start, end]``, downloading only what is needed.

    ``download(tickers, start, end)`` is the provider download (same frame
    shape as ``download_prices``). Returns ``None`` when the store is disabled
    or unavailable (failed, slower than the budget, or paused after a failure),
    so the caller downloads everything itself.
    """
    global _paused_until
    if _client is None or not tickers or time.monotonic() < _paused_until:
        return None
    try:
        return _fetch(tuple(tickers), start, end, download, _Deadline(_budget_seconds))
    except (InsForgeError, KeyError, TypeError, ValueError) as error:
        _paused_until = time.monotonic() + FAILURE_PAUSE_SECONDS
        logger.warning(
            "Price store unavailable; downloading directly for the next %d minutes: %s",
            FAILURE_PAUSE_SECONDS // 60,
            error,
        )
        return None


def _fetch(
    tickers: tuple[str, ...], start: date, end: date, download: Downloader, deadline: _Deadline
) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    coverage = _coverage(tickers, deadline)
    full: dict[str, tuple[date, date]] = {}
    tail: dict[str, tuple[date, date]] = {}
    for ticker in tickers:
        stored = coverage.get(ticker)
        if stored is None:
            full[ticker] = (start, end)
        elif stored.start > start or now - stored.full_refreshed_at > _full_refresh:
            full[ticker] = (min(start, stored.start), max(end, stored.end))
        elif stored.end >= end and (end < date.today() or now - stored.fetched_at <= _intraday_ttl):
            continue  # served from the store as is
        else:
            tail[ticker] = (stored.end - timedelta(days=OVERLAP_DAYS), max(end, stored.end))

    tail_series = _download_groups(tail, download, deadline)
    if tail_series:
        overlap_start = min(window[0] for window in tail.values())
        overlap_end = max(coverage[ticker].end for ticker in tail)
        stored_overlap = _stored_series(tuple(tail_series), overlap_start, overlap_end, deadline)
        for ticker, fresh in list(tail_series.items()):
            if _readjusted(stored_overlap.get(ticker), fresh):
                logger.info("Price history of %s was re-adjusted; downloading it in full", ticker)
                stored = coverage[ticker]
                full[ticker] = (stored.start, max(end, stored.end))
                del tail_series[ticker]
    full_series = _download_groups(full, download, deadline)

    stamp = now.isoformat()
    _write({**tail_series, **full_series}, deadline)
    _write_coverage(
        [
            {
                "ticker": ticker,
                "start_date": coverage[ticker].start.isoformat(),
                "end_date": max(tail[ticker][1], coverage[ticker].end).isoformat(),
                "fetched_at": stamp,
            }
            for ticker in tail_series
        ],
        deadline,
    )
    _write_coverage(
        [
            {
                "ticker": ticker,
                "start_date": full[ticker][0].isoformat(),
                "end_date": full[ticker][1].isoformat(),
                "fetched_at": stamp,
                "full_refreshed_at": stamp,
            }
            for ticker in full_series
        ],
        deadline,
    )
    if tail or full:
        logger.info(
            "Price store: %d stored, %d tail, %d full (%s to %s)",
            len(tickers) - len(tail) - len(full), len(tail_series), len(full_series), start, end,
        )

    series = _stored_series(tickers, start, end, deadline)
    frames = [series[ticker].rename(ticker) for ticker in tickers if ticker in series]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1, sort=True).sort_index().rename_axis(index="Fecha")
