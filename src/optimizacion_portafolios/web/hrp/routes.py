"""Hierarchical risk parity HTTP routes."""

import logging
from datetime import date
from io import BytesIO

import numpy as np
from flask import flash, render_template, request, send_file
from flask_login import current_user

from optimizacion_portafolios.web.common.notice import with_notice_html

from optimizacion_portafolios.analytics.statistics import date_years_ago
from optimizacion_portafolios.data.market_data import DOWNLOAD_ERRORS
from optimizacion_portafolios.data.catalogs import (
    ACTIVOS_HRP,
    BENCHMARKS,
    UNIVERSOS_HRP,
)
from optimizacion_portafolios.storage.profiles import get_current_profile
from optimizacion_portafolios.web.account.hooks import record_backtest
from optimizacion_portafolios.web.guia.threshold import access_view, optimization_access, optimization_text
from optimizacion_portafolios.web.auth.permissions import can
from optimizacion_portafolios.web.common import guided
from optimizacion_portafolios.web.decisions import log as decision_log
from optimizacion_portafolios.web.portfolios.snapshots import hrp_snapshot, sign
from optimizacion_portafolios.web.common.forms import (
    custom_tickers,
    normalize_ticker,
    parse_bounded_float,
    parse_bounded_int,
    parse_optional_bounded_float,
    parse_start_date,
    selected_option,
    selected_tickers,
    universes_payload,
)
from optimizacion_portafolios.models.hrp_backtest import (
    MIN_LOOKBACK,
    VOL_HYSTERESIS,
    BacktestConfig,
    engine_ready,
)
from optimizacion_portafolios.web.hrp import bp
from optimizacion_portafolios.web.hrp.services import (
    REPORT_FILENAME,
    ROLLING_SHARPE_PERIOD,
    WeightBounds,
    asset_name,
    build_hrp_report,
    run_hrp,
)

logger = logging.getLogger(__name__)
# Catalog labels per ticker, e.g. "AAPL" -> "Apple (AAPL)". Some catalogs use
# the ticker itself as label, so a descriptive label wins over a bare ticker.
_ASSET_LABELS: dict[str, str] = {}
for _label, _ticker in ACTIVOS_HRP.items():
    if _ASSET_LABELS.get(_ticker, _ticker) == _ticker:
        _ASSET_LABELS[_ticker] = _label
_DEFAULT_UNIVERSE = "Portafolio actual"
_DEFAULT_MIN_WEIGHT = "0"
_DEFAULT_MAX_WEIGHT = "100"
# Backtest form fields (percentages typed in percent) and their defaults.
BACKTEST_DEFAULTS = {
    "bt_lookback": "252",
    "bt_drift": "5",
    "bt_vol_window": "63",
    "bt_vol_threshold": "",
    "bt_fees": "0.10",
    "bt_slippage": "0.05",
    "bt_init_cash": "10000",
}


def _percent_field(field: str, label: str, default: str) -> float:
    return parse_bounded_float(
        field,
        default,
        0.0,
        100.0,
        f"El {label} debe ser numérico.",
        f"El {label} debe estar entre 0 % y 100 %.",
    ) / 100


def _is_custom_limit(value: str, default: float) -> bool:
    """True when a global limit typed in percent differs from its default."""
    try:
        return abs(float(value) - default) > 1e-9
    except (TypeError, ValueError):
        return bool(str(value).strip())


def _asset_limits(prefix: str, label: str, tickers: list[str]) -> dict[str, float]:
    limits = {}
    for ticker in tickers:
        value = parse_optional_bounded_float(
            f"{prefix}_{ticker}",
            0.0,
            100.0,
            f"El {label} de {ticker} debe ser numérico.",
            f"El {label} de {ticker} debe estar entre 0 % y 100 %.",
        )
        if value is not None:
            limits[ticker] = value / 100
    return limits


def _weight_bounds(tickers: list[str]) -> WeightBounds:
    """Global and per-asset weight limits typed in percent (empty = global)."""
    return WeightBounds(
        min_weight=_percent_field("min_weight", "peso mínimo", _DEFAULT_MIN_WEIGHT),
        max_weight=_percent_field("max_weight", "peso máximo", _DEFAULT_MAX_WEIGHT),
        asset_min_weights=_asset_limits("min", "peso mínimo", tickers),
        asset_max_weights=_asset_limits("max", "peso máximo", tickers),
    )


def _backtest_config() -> BacktestConfig | None:
    """Backtest parameters from the form, or ``None`` when it is disabled."""
    if not request.form.get("backtest"):
        return None
    defaults = BACKTEST_DEFAULTS

    def number(field, label, low, high, unit=""):
        return parse_bounded_float(
            field,
            defaults[field],
            low,
            high,
            f"{label} debe ser numérico.",
            f"{label} debe estar entre {low:g}{unit} y {high:g}{unit}.",
        )

    def whole(field, label, low, high):
        return parse_bounded_int(
            field,
            defaults[field],
            low,
            high,
            f"{label} debe ser un número entero de días.",
            f"{label} debe estar entre {low} y {high} días.",
        )

    vol_threshold = parse_optional_bounded_float(
        "bt_vol_threshold",
        1.0,
        200.0,
        "El umbral de volatilidad debe ser numérico.",
        "El umbral de volatilidad debe estar entre 1 % y 200 %.",
    )
    return BacktestConfig(
        lookback=whole("bt_lookback", "La ventana de estimación", MIN_LOOKBACK, 1260),
        drift_threshold=number("bt_drift", "El umbral de desviación", 0.5, 50, " pp") / 100,
        vol_window=whole("bt_vol_window", "La ventana de volatilidad", 5, 252),
        vol_threshold=vol_threshold / 100 if vol_threshold is not None else None,
        fees=number("bt_fees", "La comisión", 0, 5, " %") / 100,
        slippage=number("bt_slippage", "El slippage", 0, 5, " %") / 100,
        init_cash=number("bt_init_cash", "El capital inicial", 100, 1e10),
    )


def _guided_hrp():
    """HRP for novato / inversionista: predefined universe, fixed inputs."""
    access = optimization_access(get_current_profile(current_user.id))
    if not access.unlocked:
        return render_template("guia/locked.html", view=access_view(access))
    universes = guided.GUIDED_HRP_UNIVERSES
    universe_name, available, selected = guided.guided_selection(universes)
    start_date = guided.guided_start()
    max_weight = guided.GUIDED_MAX_WEIGHT_PCT
    result = None
    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            max_weight = guided.guided_max_weight()
            if len(selected) < 2:
                raise ValueError("Deja al menos dos activos para calcular HRP.")
            if universe_name == guided.OWN_UNIVERSE and len(selected) > guided.OWN_UNIVERSE_MAX_ASSETS:
                raise ValueError(f"Elige como máximo {guided.OWN_UNIVERSE_MAX_ASSETS} activos.")
            bounds = WeightBounds(max_weight=max_weight / 100)
            try:
                result = run_hrp(selected, guided.GUIDED_BENCHMARK, start_date, bounds, _ASSET_LABELS)
            except DOWNLOAD_ERRORS as error:
                logger.exception("Yahoo Finance request failed during guided HRP optimization")
                raise ValueError(f"No se pudieron descargar los datos: {error}") from error
            decision_log.record(
                "hrp",
                decision_log.hrp_weights(result),
                {
                    "mode": "guiada",
                    "universe": universe_name,
                    "tickers": result["selected_tickers"],
                    "start_date": start_date.isoformat(),
                    "benchmark": guided.GUIDED_BENCHMARK,
                    "max_weight": max_weight / 100,
                },
            )
            names = {row["ticker"]: row["name"] for row in result["allocation_rows"]}
            result["explanation"] = guided.hrp_explanation(result)
            for row in result["explanation"]["rows"]:
                row["name"] = names.get(row["ticker"])
            if can("fix_portfolio"):
                result["snapshot"] = sign(
                    hrp_snapshot(result, universe=universe_name, start_date=start_date.isoformat())
                )
        except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError) as error:
            result = None
            flash(str(error), "error")
    labels = guided.asset_labels(universes[universe_name])
    return render_template(
        "hrp/guided.html",
        cost_note=optimization_text()["nota_activos"],
        universes=universes,
        universe_names=list(universes),
        universe_name=universe_name,
        universe_descriptions=guided.UNIVERSE_DESCRIPTIONS,
        universes_payload=universes_payload(universes, guided.default_universe_selection),
        available_tickers=available,
        selected_tickers=selected,
        asset_labels=labels,
        max_weight=f"{max_weight:g}",
        start_date=start_date.isoformat(),
        benchmark_name=guided.GUIDED_BENCHMARK_NAME,
        glossary=guided.glossary(guided.GUIDED_TERMS),
        result=result,
    )


@bp.route("/hrp", methods=["GET", "POST"])
def hrp():
    if not can("advanced_params"):
        return _guided_hrp()
    today = date.today()
    default_start = date_years_ago(today)
    universe_names = list(UNIVERSOS_HRP)
    universe_name = request.values.get("universe", _DEFAULT_UNIVERSE)
    if universe_name not in UNIVERSOS_HRP:
        flash("Selecciona un universo de activos válido.", "error")
        universe_name = _DEFAULT_UNIVERSE
    available_tickers = list(UNIVERSOS_HRP[universe_name].values())
    selected = request.form.getlist("tickers") if request.method == "POST" else []
    if request.method == "POST" and request.form.get("action") == "refresh":
        selected = [ticker for ticker in selected if ticker in available_tickers]
    if not selected:
        selected = available_tickers

    benchmark_names = list(BENCHMARKS)
    benchmark_name = request.values.get("benchmark", benchmark_names[0])
    if benchmark_name not in BENCHMARKS:
        benchmark_name = benchmark_names[0]
    custom_benchmark = request.values.get("custom_benchmark", "").strip()
    start_value = request.values.get("start_date", default_start.isoformat())
    result = None

    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            selected = selected_tickers()
            allowed = set(ACTIVOS_HRP.values()) | custom_tickers()
            invalid = [ticker for ticker in selected if ticker not in allowed]
            if invalid:
                raise ValueError("Activos no disponibles: " + ", ".join(invalid))
            if len(selected) < 2:
                raise ValueError("Selecciona al menos dos activos para calcular HRP.")
            benchmark_name = selected_option(
                "benchmark", BENCHMARKS, benchmark_names[0]
            )
            benchmark_ticker = (
                normalize_ticker(custom_benchmark)
                if custom_benchmark
                else BENCHMARKS[benchmark_name]
            )
            start_date = parse_start_date("start_date", default_start)
            bounds = _weight_bounds(selected)
            download_requested = request.form.get("action") == "download_report"
            backtest = None if download_requested else _backtest_config()
            try:
                if download_requested:
                    report, missing = build_hrp_report(
                        selected, benchmark_ticker, start_date, bounds
                    )
                else:
                    result = run_hrp(
                        selected,
                        benchmark_ticker,
                        start_date,
                        bounds,
                        _ASSET_LABELS,
                        backtest=backtest,
                    )
                    missing = result["missing"]
                    result["snapshot"] = sign(
                        hrp_snapshot(
                            result, universe=universe_name, start_date=start_date.isoformat()
                        )
                    )
                    record_backtest(result["backtest"])
                    decision_log.record(
                        "hrp",
                        decision_log.hrp_weights(result),
                        {
                            "mode": "completa",
                            "universe": universe_name,
                            "tickers": result["selected_tickers"],
                            "start_date": start_date.isoformat(),
                            "benchmark": benchmark_ticker,
                            "bounds": result["summary"]["bounds"],
                            "backtest": backtest is not None,
                        },
                    )
            except DOWNLOAD_ERRORS as error:
                logger.exception("Yahoo Finance request failed during HRP optimization")
                raise ValueError(f"No se pudieron descargar los datos: {error}") from error
            if missing and download_requested:
                flash(
                    "Yahoo Finance no devolvió datos para: " + ", ".join(missing),
                    "warning",
                )
            if download_requested:
                return send_file(
                    BytesIO(with_notice_html(report)),
                    mimetype="text/html",
                    as_attachment=True,
                    download_name=REPORT_FILENAME,
                )
        except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError) as error:
            flash(str(error), "error")

    asset_limits = {
        ticker: {
            "min": request.form.get(f"min_{ticker}", "").strip(),
            "max": request.form.get(f"max_{ticker}", "").strip(),
        }
        for ticker in selected
    }
    min_weight = request.values.get("min_weight", _DEFAULT_MIN_WEIGHT)
    max_weight = request.values.get("max_weight", _DEFAULT_MAX_WEIGHT)
    has_asset_limits = any(
        limits["min"] or limits["max"] for limits in asset_limits.values()
    )
    backtest_values = {
        field: request.form.get(field, default).strip() if request.method == "POST" else default
        for field, default in BACKTEST_DEFAULTS.items()
    }
    return render_template(
        "hrp/hrp.html",
        backtest_enabled=request.method != "POST" or bool(request.form.get("backtest")),
        backtest_values=backtest_values,
        backtest_engine_ready=engine_ready(),
        vol_hysteresis=VOL_HYSTERESIS,
        today=today.isoformat(),
        default_start=start_value,
        universe_names=universe_names,
        universe_name=universe_name,
        available_tickers=available_tickers,
        selected_tickers=selected,
        benchmark_names=benchmark_names,
        benchmark_name=benchmark_name,
        custom_benchmark=custom_benchmark,
        min_weight=min_weight,
        max_weight=max_weight,
        asset_limits=asset_limits,
        has_asset_limits=has_asset_limits,
        limits_open=has_asset_limits
        or _is_custom_limit(min_weight, 0.0)
        or _is_custom_limit(max_weight, 100.0),
        asset_labels={ticker: _ASSET_LABELS.get(ticker, ticker) for ticker in available_tickers},
        asset_names={ticker: asset_name(ticker, _ASSET_LABELS.get(ticker)) for ticker in selected},
        universes=universes_payload(UNIVERSOS_HRP),
        custom_tickers_value=request.values.get("custom_tickers", ""),
        rolling_sharpe_period=ROLLING_SHARPE_PERIOD,
        result=result,
    )
