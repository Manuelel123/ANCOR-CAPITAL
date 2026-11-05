"""Mis portafolios workflows: fix a model result and value it over time."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from optimizacion_portafolios.analytics.alerts import LEVEL_LABELS, overall_level
from optimizacion_portafolios.analytics.tracking import (
    ROLLING_SHARPE_WINDOW,
    Segment,
    business_days,
    holding_values,
    initial_positions,
    last_prices,
    rebased,
    tracking_metrics,
    tracking_summary,
)
from optimizacion_portafolios.data.catalogs import BENCHMARKS
from optimizacion_portafolios.data.fx import prices_in_usd
from optimizacion_portafolios.data.market_data import DOWNLOAD_ERRORS, download_prices
from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.storage.models import DailySnapshot, Portfolio
from optimizacion_portafolios.storage.repositories import HoldingData, TransactionData
from optimizacion_portafolios.web.common.charts import (
    contribution_chart,
    drawdown_chart,
    rolling_lines_chart,
    rolling_sharpe_chart,
    tracking_value_chart,
    weights_vs_target_chart,
)
from optimizacion_portafolios.web.portfolios import alerting
from optimizacion_portafolios.web.portfolios.snapshots import MODEL_LABELS, default_name

logger = logging.getLogger(__name__)

DEFAULT_CAPITAL = 100_000.0
DEFAULT_COMMISSION_BPS = 10.0
# Calendar days searched backwards for the closing prices used to fix a portfolio.
FIX_PRICE_LOOKBACK_DAYS = 14
STATUS_LABELS = {"active": "Activo", "archived": "Archivado"}
REASON_LABELS = {
    "fix": "Fijado como óptimo",
    "rebalance": "Rebalanceo",
    "reoptimize": "Re-optimización",
    "remove": "Eliminación de activos",
    "add": "Alta de activos",
}
_BENCHMARK_LABELS = {ticker: label for label, ticker in BENCHMARKS.items()}


def benchmark_options(current: str) -> list[tuple[str, str]]:
    """``(ticker, label)`` choices of the fixing form (the result's own first)."""
    options = [(ticker, label) for label, ticker in BENCHMARKS.items()]
    if current and current not in _BENCHMARK_LABELS:
        options.insert(0, (current, current))
    return options


def fix_form(snapshot: dict) -> dict:
    """Template data of the "Establecer como portafolio óptimo" form."""
    weights = snapshot["weights"]
    return {
        "model": MODEL_LABELS[snapshot["model"]],
        "default_name": default_name(snapshot),
        "capital": DEFAULT_CAPITAL,
        "commission_bps": DEFAULT_COMMISSION_BPS,
        "benchmark": snapshot.get("benchmark") or "SPY",
        "benchmarks": benchmark_options(snapshot.get("benchmark") or ""),
        "weights": sorted(weights.items(), key=lambda item: -item[1]),
    }


def usd_prices(tickers: tuple[str, ...], start: date, end: date) -> pd.DataFrame:
    raw = download_prices(tickers, start, end)
    available = [ticker for ticker in tickers if ticker in raw.columns and raw[ticker].notna().any()]
    return prices_in_usd(raw.loc[:, available], start, end) if available else pd.DataFrame()


def fix_portfolio(
    user_id: str,
    snapshot: dict,
    *,
    name: str,
    capital: float,
    benchmark: str,
    commission_bps: float,
    alert_rules: dict | None = None,
    today: date | None = None,
) -> Portfolio:
    """Store the snapshot as version 1, bought at the latest available closes.

    Raises ``ValueError`` (Spanish message) when a ticker or the benchmark has
    no recent price.
    """
    today = today or date.today()
    weights = {ticker: float(weight) for ticker, weight in snapshot["weights"].items()}
    tickers = tuple(weights)
    start = today - timedelta(days=FIX_PRICE_LOOKBACK_DAYS)
    try:
        prices = usd_prices(tickers, start, today)
        benchmark_prices = download_prices((benchmark,), start, today)
    except DOWNLOAD_ERRORS as error:
        raise ValueError(f"No se pudieron descargar los precios de fijación: {error}") from error
    missing = [ticker for ticker in tickers if ticker not in prices.columns]
    if missing:
        raise ValueError("No hay precios recientes para: " + ", ".join(missing))
    if benchmark not in benchmark_prices or benchmark_prices[benchmark].dropna().empty:
        raise ValueError(f"No hay precios recientes para el benchmark {benchmark}.")
    price_day, closes = last_prices(business_days(prices.sort_index().ffill()))
    positions = initial_positions(capital, weights, closes, commission_bps)
    total_weight = sum(weights.values())
    holdings = [
        HoldingData(ticker, weights[ticker] / total_weight, positions.units[ticker], closes[ticker])
        for ticker in tickers
    ]
    transactions = [
        TransactionData(
            ticker,
            "buy" if positions.units[ticker] >= 0 else "sell",
            abs(positions.units[ticker]),
            closes[ticker],
            abs(positions.amounts[ticker]),
            positions.cost * abs(weights[ticker]) / sum(abs(w) for w in weights.values()),
        )
        for ticker in tickers
    ]
    params = {
        **(snapshot.get("params") or {}),
        "metrics_at_fixing": snapshot.get("metrics") or {},
        "price_date": price_day.date().isoformat(),
        "currency": "USD",
    }
    portfolio = repositories.create_portfolio(
        user_id,
        name=name,
        model=snapshot["model"],
        benchmark=benchmark,
        initial_capital=capital,
        tracking_start=price_day.date(),
        holdings=holdings,
        transactions=transactions,
        params=params,
        commission_bps=commission_bps,
        cost=positions.cost,
    )
    repositories.set_alert_rules(portfolio, alert_rules or alerting.default_rules())
    return portfolio


# ------------------------------------------------------------------ list/detail
def _summary(portfolio: Portfolio) -> dict:
    version = portfolio.current_version
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "model": MODEL_LABELS.get(portfolio.model, portfolio.model),
        "status": portfolio.status,
        "status_label": STATUS_LABELS.get(portfolio.status, portfolio.status),
        "benchmark": portfolio.benchmark,
        "benchmark_label": _BENCHMARK_LABELS.get(portfolio.benchmark, portfolio.benchmark),
        "initial_capital": portfolio.initial_capital,
        "commission_bps": portfolio.commission_bps,
        "tracking_start": portfolio.tracking_start.isoformat(),
        "version_number": version.number if version else 0,
        "asset_count": len(version.holdings) if version else 0,
        "tickers": [holding.ticker for holding in version.holdings] if version else [],
        "visible": portfolio.visible_usuarios,
    }


def portfolio_header(portfolio: Portfolio) -> dict:
    """Summary for sub-pages (alerts, management, versions)."""
    return _summary(portfolio)


def public_summary(portfolio: Portfolio) -> dict:
    """Card of a gestor's portfolio on the home page; its panel has the rest.

    Uses the last stored valuation, so the home page never downloads prices.
    """
    version = portfolio.current_version
    # order_by(None): the relationship already sorts by day, oldest first.
    last = portfolio.snapshots.order_by(None).order_by(DailySnapshot.day.desc()).first()
    holdings = sorted(version.holdings, key=lambda holding: -holding.target_weight) if version else []

    def total_return(value):
        return None if value is None else value / portfolio.initial_capital - 1

    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "benchmark": _BENCHMARK_LABELS.get(portfolio.benchmark, portfolio.benchmark),
        "tracking_start": portfolio.tracking_start.isoformat(),
        "weights": [(holding.ticker, holding.target_weight) for holding in holdings],
        "total_return": total_return(last.value) if last else None,
        "benchmark_return": total_return(last.benchmark_value) if last else None,
        "valued_on": last.day.isoformat() if last else None,
    }


def public_portfolios() -> list[dict]:
    return [public_summary(portfolio) for portfolio in repositories.visible_gestor_portfolios()]


def portfolio_list(user_id: str) -> list[dict]:
    levels = repositories.open_alert_levels(user_id)
    rows = []
    for portfolio in repositories.list_portfolios(user_id):
        row = _summary(portfolio)
        open_levels = levels.get(portfolio.id, []) if portfolio.is_active else []
        level = overall_level(open_levels) if portfolio.is_active else None
        row["alert_level"], row["alert_label"] = level, LEVEL_LABELS.get(level, "")
        row["alert_count"] = len(open_levels)
        rows.append(row)
    return rows


def segments(portfolio: Portfolio) -> list[Segment]:
    return [
        Segment(pd.Timestamp(version.effective_date), version.units, REASON_LABELS.get(version.reason, version.reason))
        for version in portfolio.versions
    ]


def risk_free_rate(portfolio: Portfolio) -> float:
    """Yearly rf of the model inputs (Black-Litterman); 0 when not given."""
    first = portfolio.versions[0].params if portfolio.versions else {}
    try:
        return float((first or {}).get("risk_free_rate") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def cumulative_turnover(portfolio: Portfolio) -> float:
    return float(sum(version.turnover for version in portfolio.versions[1:]))


def valuation(portfolio: Portfolio, today: date | None = None) -> dict:
    """Daily buy-and-hold valuation since the fixing date (USD, business days).

    Returns ``holdings`` (per-asset values), ``total``, ``benchmark`` (rebased
    to the initial capital) and the asset ``prices``; raises ``ValueError``.
    """
    end = today or date.today()
    start = portfolio.tracking_start
    tickers = tuple(sorted({h.ticker for version in portfolio.versions for h in version.holdings}))
    try:
        prices = usd_prices(tickers, start, end)
        benchmark = usd_prices((portfolio.benchmark,), start, end)
    except DOWNLOAD_ERRORS as error:
        raise ValueError(f"No se pudieron descargar los precios: {error}") from error
    holdings = business_days(holding_values(prices, segments(portfolio)))
    if holdings.empty:
        raise ValueError("Todavía no hay precios desde la fecha de fijación.")
    total = holdings.sum(axis=1).rename("Portafolio")
    benchmark_series = (
        rebased(business_days(benchmark[portfolio.benchmark]), portfolio.initial_capital, pd.Timestamp(start))
        if portfolio.benchmark in benchmark.columns
        else pd.Series(dtype=float)
    )
    return {
        "holdings": holdings,
        "total": total,
        "benchmark": benchmark_series,
        "prices": business_days(prices.sort_index().ffill()).reindex(holdings.index),
        "raw_prices": business_days(prices.sort_index()),
    }


def analyze(portfolio: Portfolio, today: date | None = None) -> dict:
    """Valuation plus every tracking metric (see ``analytics.tracking``)."""
    valued = valuation(portfolio, today)
    metrics = tracking_metrics(
        valued["total"],
        valued["holdings"],
        valued["prices"],
        valued["benchmark"],
        portfolio.current_version.target_weights,
        rf=risk_free_rate(portfolio),
        turnover=cumulative_turnover(portfolio),
        raw_prices=valued["raw_prices"],
    )
    return {**valued, "metrics": metrics}


def snapshot_rows(portfolio: Portfolio, analysis: dict) -> list[dict]:
    """One snapshot per valued day; only the last day carries the metrics."""
    holdings, total = analysis["holdings"], analysis["total"]
    benchmark = analysis["benchmark"].reindex(total.index).ffill()
    weights = holdings.div(total, axis=0)
    last_day = total.index[-1]
    rows = []
    for day, value in total.items():
        day_weights = weights.loc[day]
        rows.append(
            {
                "day": day.date(),
                "value": float(value),
                "benchmark_value": float(benchmark.loc[day]) if pd.notna(benchmark.get(day)) else None,
                "weights": {ticker: float(w) for ticker, w in day_weights.items() if abs(w) > 0},
                "metrics": analysis["metrics"]["scalars"] if day == last_day else None,
            }
        )
    return rows


def record_snapshots(portfolio: Portfolio, analysis: dict) -> int:
    """Store missing daily snapshots and refresh the latest day's one."""
    rows = snapshot_rows(portfolio, analysis)
    return repositories.save_snapshots(portfolio, rows, refresh_day=rows[-1]["day"])


def snapshot_all(today: date | None = None) -> list[tuple[Portfolio, int | str]]:
    """Snapshot every active portfolio; returns ``(portfolio, rows | error)``."""
    outcome = []
    for portfolio in repositories.active_portfolios():
        try:
            analysis = analyze(portfolio, today)
            written = record_snapshots(portfolio, analysis)
            alerting.evaluate_portfolio(portfolio, analysis)
            outcome.append((portfolio, written))
        except ValueError as error:
            logger.warning("Snapshot of portfolio %s failed: %s", portfolio.id, error)
            outcome.append((portfolio, str(error)))
    return outcome


# ---------------------------------------------------------------- view model
def _pct(value, decimals: int = 2) -> str:
    return "N/D" if value is None else f"{value * 100:.{decimals}f} %"


def _ratio(value) -> str:
    return "N/D" if value is None else f"{value:.2f}"


def _days(value) -> str:
    return "N/D" if value is None else f"{int(value)} días"


def metric_groups(scalars: dict) -> list[dict]:
    """Dashboard metric cards grouped by category, already formatted."""
    s = scalars
    return [
        {
            "title": "Rentabilidad",
            "metrics": [
                ("Retorno acumulado", _pct(s["cumulative_return"])),
                ("Mes en curso", _pct(s["return_mtd"])),
                ("Año en curso (YTD)", _pct(s["return_ytd"])),
                ("Último año", _pct(s["return_one_year"])),
                ("CAGR", _pct(s["cagr"])),
            ],
        },
        {
            "title": "Riesgo",
            "metrics": [
                ("Volatilidad anualizada", _pct(s["volatility"])),
                ("Volatilidad móvil 21 d", _pct(s["rolling_volatility_21"])),
                ("Volatilidad móvil 63 d", _pct(s["rolling_volatility_63"])),
                ("Drawdown actual", _pct(s["drawdown"])),
                ("Máximo drawdown", _pct(s["max_drawdown"])),
                ("Duración máx. del drawdown", _days(s["max_drawdown_days"])),
                ("VaR 95 % diario (histórico)", _pct(s["var_95"])),
                ("CVaR 95 % diario (histórico)", _pct(s["cvar_95"])),
                ("Semidesviación anualizada", _pct(s["semideviation"])),
            ],
        },
        {
            "title": "Ajustadas por riesgo",
            "metrics": [
                ("Ratio de Sharpe", _ratio(s["sharpe"])),
                ("Ratio de Sortino", _ratio(s["sortino"])),
                ("Ratio de Calmar", _ratio(s["calmar"])),
                ("Sharpe móvil 126 d", _ratio(s["rolling_sharpe_126"])),
            ],
        },
        {
            "title": "Frente al benchmark",
            "metrics": [
                ("Beta", _ratio(s["beta"])),
                ("Alpha de Jensen (anual)", _pct(s["alpha"])),
                ("Tracking error", _pct(s["tracking_error"])),
                ("Information ratio", _ratio(s["information_ratio"])),
                ("Captura alcista", _pct(s["upside_capture"], 0)),
                ("Captura bajista", _pct(s["downside_capture"], 0)),
            ],
        },
        {
            "title": "Estructura",
            "metrics": [
                ("Desviación máxima de peso", "N/D" if s["max_abs_drift"] is None else f"{s['max_abs_drift'] * 100:.2f} pp"),
                ("Índice Herfindahl", "N/D" if s["herfindahl"] is None else f"{s['herfindahl']:.3f}"),
                ("Número efectivo de activos", _ratio(s["effective_assets"])),
                ("Correlación promedio 63 d", _ratio(s["average_correlation"])),
                ("Mayor contribución al riesgo", _pct(s["max_risk_share"], 1)),
                ("Rotación acumulada", _pct(s["turnover"], 1)),
            ],
        },
    ]


def _charts(portfolio: Portfolio, analysis: dict) -> dict:
    metrics = analysis["metrics"]
    series = metrics["series"]
    weights = metrics["weights"]
    benchmark_label = _BENCHMARK_LABELS.get(portfolio.benchmark, portfolio.benchmark)
    contributions = pd.DataFrame(
        {"Contribución al riesgo": metrics["risk_contributions"], "Peso actual": weights}
    ).dropna(subset=["Contribución al riesgo"])
    charts = {
        "value": tracking_value_chart(
            analysis["total"],
            analysis["benchmark"],
            [(v.effective_date, REASON_LABELS.get(v.reason, v.reason)) for v in portfolio.versions[1:]],
            benchmark_name=benchmark_label,
        ),
        "drawdown": drawdown_chart(series["returns"]),
        "volatility": rolling_lines_chart(
            {f"{window} días": values for window, values in series["rolling_volatility"].items()}
        ),
        "sharpe": rolling_sharpe_chart(
            series["returns"].rename("Portafolio"),
            series["benchmark_returns"].rename(benchmark_label),
            ROLLING_SHARPE_WINDOW,
        ),
        "correlation": rolling_lines_chart({"Correlación promedio": series["average_correlation"]}, ".2f"),
        "weights": weights_vs_target_chart(weights, portfolio.current_version.target_weights),
        "risk": contribution_chart(contributions, "Contribución al riesgo", compare="Peso actual")
        if not contributions.empty
        else None,
        "returns": contribution_chart(
            metrics["return_contributions"].to_frame(), "Contribución al retorno"
        )
        if not metrics["return_contributions"].empty
        else None,
    }
    return {key: chart for key, chart in charts.items() if chart is not None}


def portfolio_detail(portfolio: Portfolio, today: date | None = None) -> dict:
    """Dashboard view model; stores the snapshots missing up to the last valued day."""
    version = portfolio.current_version
    view = {
        **_summary(portfolio),
        "version": {
            "number": version.number,
            "effective_date": version.effective_date.isoformat(),
            "reason": REASON_LABELS.get(version.reason, version.reason),
            "value_after": version.value_after,
            "cost": version.cost,
        },
        "params": version.params or {},
        "tracking": None,
        "tracking_error": None,
    }
    try:
        analysis = analyze(portfolio, today)
    except ValueError as error:
        view["tracking_error"] = str(error)
        analysis = None
    current = drift = last_close = pd.Series(dtype=float)
    signals = None
    if analysis is not None:
        if portfolio.is_active:
            record_snapshots(portfolio, analysis)
            signals = alerting.evaluate_portfolio(portfolio, analysis)
        metrics = analysis["metrics"]
        current, drift = metrics["weights"], metrics["drift"]
        last_close = analysis["prices"].iloc[-1]
        view["tracking"] = {
            **tracking_summary(analysis["total"], analysis["benchmark"], portfolio.initial_capital),
            "metrics": metrics["scalars"],
            "groups": metric_groups(metrics["scalars"]),
            "charts": _charts(portfolio, analysis),
        }
    view["alerts"] = alerting.status_view(portfolio, signals)
    view["snapshots"] = [
        {"day": s.day.isoformat(), "value": s.value, "benchmark_value": s.benchmark_value, "has_metrics": bool(s.metrics)}
        for s in repositories.latest_snapshots(portfolio, 10)
    ]
    view["holdings"] = []
    for holding in version.holdings:
        price = last_close.get(holding.ticker)
        price = float(price) if price is not None and pd.notna(price) else None
        view["holdings"].append(
            {
                "ticker": holding.ticker,
                "target_weight": holding.target_weight,
                "current_weight": float(current[holding.ticker]) if holding.ticker in current else None,
                "drift": float(drift[holding.ticker]) if holding.ticker in drift else None,
                "units": holding.units,
                "entry_price": holding.entry_price,
                "last_price": price,
                "value": holding.units * price if price is not None else None,
            }
        )
    return view


# ------------------------------------------------------------------ versions
_METHOD_LABELS = {
    "targets": "a los pesos objetivo",
    "reoptimize": "re-optimizando",
    "proportional": "redistribución proporcional",
    "manual": "peso manual",
}


def _version_row(version) -> dict:
    change = (version.params or {}).get("change") or {}
    details = []
    if change.get("method"):
        details.append(_METHOD_LABELS.get(change["method"], change["method"]))
    if change.get("removed"):
        details.append("sin " + ", ".join(change["removed"]))
    if change.get("added"):
        details.append("con " + ", ".join(change["added"]))
    return {
        "number": version.number,
        "effective_date": version.effective_date.isoformat(),
        "reason": REASON_LABELS.get(version.reason, version.reason),
        "details": " · ".join(details),
        "note": version.note,
        "value_before": version.value_before,
        "value_after": version.value_after,
        "cost": version.cost,
        "turnover": version.turnover if version.number > 1 else None,
        "assets": len(version.holdings),
        "trades": len(version.transactions),
    }


def versions_view(portfolio: Portfolio) -> list[dict]:
    return [_version_row(version) for version in reversed(portfolio.versions)]


def compare_versions(portfolio: Portfolio, first: int, second: int) -> dict:
    """Weights and units of two versions side by side."""
    by_number = {version.number: version for version in portfolio.versions}
    a, b = by_number[first], by_number[second]
    tickers = sorted(set(a.target_weights) | set(b.target_weights), key=lambda t: -b.target_weights.get(t, a.target_weights.get(t, 0.0)))
    return {
        "a": _version_row(a),
        "b": _version_row(b),
        "rows": [
            {
                "ticker": ticker,
                "weight_a": a.target_weights.get(ticker),
                "weight_b": b.target_weights.get(ticker),
                "change": b.target_weights.get(ticker, 0.0) - a.target_weights.get(ticker, 0.0),
                "units_a": a.units.get(ticker),
                "units_b": b.units.get(ticker),
                "status": "nuevo" if ticker not in a.target_weights else "eliminado" if ticker not in b.target_weights else "",
            }
            for ticker in tickers
        ],
    }
