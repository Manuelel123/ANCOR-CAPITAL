"""Signed result snapshots carried by "Establecer como portafolio óptimo".

An HRP or Black-Litterman result page embeds its outcome (model, weights,
metrics and every input parameter) as a signed token, so fixing the portfolio
needs no recalculation and stores only what the server computed itself.
Tokens expire after a day.
"""

from __future__ import annotations

import math
from datetime import date

from itsdangerous import BadSignature

from optimizacion_portafolios.web.common.signing import serializer

_SALT = "atlas-portfolio-snapshot"
MAX_AGE_SECONDS = 24 * 60 * 60
MODEL_LABELS = {"hrp": "HRP", "black_litterman": "Black-Litterman"}


def _clean(value):
    """Plain JSON value: numpy scalars become floats and NaN becomes ``None``."""
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, (str, bool)) or value is None:
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else None


def sign(snapshot: dict) -> str:
    return serializer(_SALT).dumps(_clean(snapshot))


def unsign(token: str) -> dict | None:
    try:
        snapshot = serializer(_SALT).loads(token, max_age=MAX_AGE_SECONDS)
    except BadSignature:
        return None
    if not isinstance(snapshot, dict) or snapshot.get("model") not in MODEL_LABELS:
        return None
    weights = snapshot.get("weights")
    if not isinstance(weights, dict) or not weights:
        return None
    return snapshot


def copied_from(snapshot: dict) -> str | None:
    """Name of the gestor's portfolio this snapshot copies, if any."""
    return (snapshot.get("params") or {}).get("copied_from")


def default_name(snapshot: dict) -> str:
    if copied_from(snapshot):
        return f"Copia de {copied_from(snapshot)}"[:120]
    count = len(snapshot.get("weights") or {})
    return f"{MODEL_LABELS[snapshot['model']]} · {count} activos · {date.today().isoformat()}"


def hrp_snapshot(result: dict, *, universe: str, start_date: str) -> dict:
    summary = result["summary"]
    return {
        "model": "hrp",
        "benchmark": result["benchmark"],
        "weights": summary["weights"],
        "metrics": summary["metrics"],
        "params": {
            "universe": universe,
            "tickers": list(result["selected_tickers"]),
            "start_date": start_date,
            "data_start": result["start"],
            "data_end": result["end"],
            "bounds": summary["bounds"],
        },
    }


def copy_snapshot(portfolio) -> dict:
    """Same weights and benchmark as ``portfolio``, to fix at today's prices."""
    return {
        "model": portfolio.model,
        "benchmark": portfolio.benchmark,
        "weights": {holding.ticker: holding.target_weight for holding in portfolio.current_version.holdings},
        "metrics": {},
        "params": {"copied_from": portfolio.name, "tickers": [h.ticker for h in portfolio.current_version.holdings]},
    }


def black_litterman_snapshot(
    result: dict, *, universe: str, objective: str, benchmark: str, risk_free_rate: float
) -> dict:
    rows = result["allocation_rows"]
    history = result.get("history") or {}
    window = result["window"]
    return {
        "model": "black_litterman",
        "benchmark": benchmark,
        "weights": {row["ticker"]: row["weight"] for row in rows},
        "metrics": {
            "expected_return": result["expected_return"],
            "volatility": result["volatility"],
            "sharpe": result["sharpe"],
            "sortino": result["sortino"],
            "max_drawdown": history.get("max_drawdown"),
        },
        "params": {
            "universe": universe,
            "tickers": [row["ticker"] for row in rows],
            "objective": objective,
            "risk_free_rate": risk_free_rate,
            "market_benchmark": benchmark,
            "views": {row["ticker"]: row["view"] for row in rows},
            "market_weights": {row["ticker"]: row["market_weight"] for row in rows},
            "window_years": window["years"],
            "data_start": window["effective_start_iso"],
            "data_end": window["effective_end_iso"],
        },
    }
