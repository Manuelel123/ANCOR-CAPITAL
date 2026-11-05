"""Guided mode of HRP, Black-Litterman and the comparison (novato / inversionista).

The guided pages only let the user pick a predefined universe (and, from
``inversionista``, exclude assets and cap the weight per asset). Every other
input is fixed here, so a posted form cannot change it. The functions below
only *present* results the models already computed: they turn weights,
volatilities, correlations and market weights into plain-language sentences.
"""

from __future__ import annotations

from datetime import date
from statistics import median

from flask import flash, request

from optimizacion_portafolios.analytics.statistics import date_years_ago
from optimizacion_portafolios.data.catalogs import (
    ACTIVOS,
    BENCHMARKS,
    COMMODITIES,
    CRIPTOMONEDAS,
    INDICES,
    PORTAFOLIO_IMAGEN,
)

# Universes offered in the guided pages. Black-Litterman needs a market
# capitalization per asset, so futures (commodities) are HRP-only.
GUIDED_UNIVERSES: dict[str, dict[str, str]] = {
    "Acciones y Bitcoin": ACTIVOS,
    "Sectores de EE. UU.": INDICES,
    "Criptomonedas": CRIPTOMONEDAS,
    "Portafolio actual": PORTAFOLIO_IMAGEN,
}
GUIDED_HRP_UNIVERSES: dict[str, dict[str, str]] = {
    **GUIDED_UNIVERSES,
    "Criptomonedas y commodities": {**CRIPTOMONEDAS, **COMMODITIES},
}
UNIVERSE_DESCRIPTIONS = {
    "Acciones y Bitcoin": "Tres grandes tecnológicas y Bitcoin: buen ejemplo de activos parecidos frente a uno muy distinto.",
    "Sectores de EE. UU.": "Fondos que replican sectores de la bolsa de EE. UU. (salud, finanzas, tecnología, energía).",
    "Criptomonedas": "Cuatro criptomonedas: muy volátiles y bastante correlacionadas entre sí.",
    "Portafolio actual": "Una mezcla de sectores, acciones, oro y fintech (19 activos).",
    "Criptomonedas y commodities": "Criptomonedas junto a oro, plata, petróleo, gas y cobre.",
}
GUIDED_DEFAULT_UNIVERSE = "Acciones y Bitcoin"

# Fixed inputs of the guided mode (the gestor's full pages keep every option).
GUIDED_YEARS = 2  # same window as Black-Litterman, so both methods see the same data
GUIDED_BENCHMARK_NAME = "S&P 500 (SPY)"
GUIDED_BENCHMARK = BENCHMARKS[GUIDED_BENCHMARK_NAME]
GUIDED_OBJECTIVE = "Máximo ratio de Sharpe"
GUIDED_RISK_FREE_PCT = 2.0
GUIDED_MAX_WEIGHT_PCT = 100.0

_NEAR = 0.15  # relative band around the median volatility that counts as "intermedia"
# Average correlation bands of the explanation (absolute, Pearson).
LOW_CORRELATION = 0.3
HIGH_CORRELATION = 0.6


def guided_start(today: date | None = None) -> date:
    return date_years_ago(today or date.today(), GUIDED_YEARS)


def guided_selection(universes: dict, default_universe: str = GUIDED_DEFAULT_UNIVERSE) -> tuple[str, list[str], list[str]]:
    """``(universe, available, selected)`` of a guided form.

    Only predefined universes; an novato always gets every asset of the
    universe, an inversionista may uncheck (exclude) some. Anything else in
    the form (custom tickers, dates, per-asset limits, views...) is ignored.
    """
    from optimizacion_portafolios.web.auth.permissions import can

    universe_name = request.values.get("universe", default_universe)
    if universe_name not in universes:
        if request.method == "POST":
            flash("Selecciona uno de los universos disponibles.", "error")
        universe_name = default_universe
    available = list(dict.fromkeys(universes[universe_name].values()))
    selected = available
    if request.method == "POST" and can("exclude_assets") and request.form.get("action") != "refresh":
        checked = set(request.form.getlist("tickers"))
        selected = [ticker for ticker in available if ticker in checked]
    return universe_name, available, selected


def guided_max_weight() -> float:
    """Maximum weight per asset in percent: from the form for inversionista, else 100."""
    from optimizacion_portafolios.web.auth.permissions import can
    from optimizacion_portafolios.web.common.forms import parse_bounded_float

    if not can("weight_limits"):
        return GUIDED_MAX_WEIGHT_PCT
    return parse_bounded_float(
        "max_weight",
        f"{GUIDED_MAX_WEIGHT_PCT:g}",
        1.0,
        100.0,
        "El peso máximo por activo debe ser numérico.",
        "El peso máximo por activo debe estar entre 1 % y 100 %.",
    )


def run_neutral_black_litterman(tickers: list[str], names: dict[str, str]) -> dict:
    """Black-Litterman with neutral views (equal to the equilibrium returns).

    The equilibrium (prior) does not depend on the views, so a first run with
    the page's default view yields it; the second run uses it as the view of
    every asset. Both runs call the unchanged ``run_black_litterman`` with the
    fixed guided inputs (2-year window, SPY, máximo Sharpe, 2 % rate).
    """
    from optimizacion_portafolios.web.black_litterman.services import DEFAULT_VIEW, run_black_litterman

    def run(assets, views):
        return run_black_litterman(
            assets,
            views,
            GUIDED_OBJECTIVE,
            GUIDED_RISK_FREE_PCT / 100,
            GUIDED_BENCHMARK,
            guided_start(),
            names,
            False,
            benchmark_label=GUIDED_BENCHMARK_NAME,
        )

    first = run(tickers, {ticker: float(DEFAULT_VIEW) / 100 for ticker in tickers})
    views = neutral_views(first)
    result = run(list(views), views)
    result["neutral_views"] = views
    return result


def asset_labels(universe: dict[str, str]) -> dict[str, str]:
    """``{ticker: "Apple (AAPL)"}`` of a catalog universe."""
    return {ticker: label for label, ticker in universe.items()}


def _level(value: float, reference: float, low: str, mid: str, high: str) -> str:
    if reference <= 0:
        return mid
    ratio = value / reference
    if ratio > 1 + _NEAR:
        return high
    if ratio < 1 - _NEAR:
        return low
    return mid


def _pct(value: float, decimals: int = 1) -> str:
    return f"{value * 100:.{decimals}f} %".replace(".", ",")


def _points(value: float) -> str:
    return f"{value * 100:+.1f} pp".replace(".", ",")


def hrp_explanation(result: dict) -> dict:
    """Plain-language reasons for each HRP weight (from ``result["asset_stats"]``)."""
    stats = result.get("asset_stats") or []
    if not stats:
        return {"rows": [], "summary": ""}
    equal = 1 / len(stats)
    vols = [row["volatility"] for row in stats if row["volatility"] is not None]
    vol_ref = median(vols) if vols else 0.0
    rows = []
    for row in stats:
        weight, vol, corr = row["weight"], row["volatility"], row["avg_correlation"]
        parts = [
            f"Recibe {_pct(weight)}, "
            + ("más" if weight > equal * 1.05 else "menos" if weight < equal * 0.95 else "casi lo mismo")
            + f" que un reparto igual ({_pct(equal)})."
        ]
        vol_level = None
        if vol is not None:
            vol_level = _level(vol, vol_ref, "baja", "intermedia", "alta")
            parts.append(f"Su volatilidad anual es {_pct(vol)}, {vol_level} frente a los demás activos.")
            if vol_level == "alta":
                parts.append("HRP le da menos peso para que sus subidas y bajadas no dominen el portafolio.")
            elif vol_level == "baja":
                parts.append("HRP premia a los activos más estables con más peso.")
        if corr is not None:
            text = f"Su correlación promedio con el resto es {corr:.2f}".replace(".", ",")
            if corr >= HIGH_CORRELATION:
                parts.append(text + " (alta): se mueve parecido a otros, así que HRP lo agrupa con ellos y el peso de ese grupo se reparte entre varios.")
            elif corr < LOW_CORRELATION:
                parts.append(text + " (baja): se mueve bastante distinto del resto, y eso es justo lo que ayuda a diversificar.")
            else:
                parts.append(text + " (media): se mueve en parte como los demás.")
        if row.get("risk_share") is not None:
            parts.append(f"Aporta {_pct(row['risk_share'])} del riesgo total del portafolio.")
        rows.append({**row, "weight_text": _pct(weight, 2), "text": " ".join(parts), "vol_level": vol_level})
    top = max(stats, key=lambda item: item["weight"])
    summary = (
        "HRP no intenta adivinar qué activo subirá más. Agrupa los activos que se mueven parecido "
        "y reparte el capital para que cada grupo aporte un riesgo comparable: los más estables y "
        f"los que se mueven distinto reciben más. Aquí el mayor peso es {top['ticker']} ({_pct(top['weight'])})."
    )
    return {"rows": rows, "summary": summary}


def black_litterman_explanation(result: dict) -> dict:
    """Plain-language reasons for each Black-Litterman weight (neutral views)."""
    rows = []
    for row in result.get("allocation_rows") or []:
        weight, market, tilt = row["weight"], row["market_weight"], row["tilt"]
        parts = [
            f"El mercado le da {_pct(market)} por su tamaño (capitalización bursátil).",
            f"Sin opiniones propias, su retorno esperado es el de equilibrio: {_pct(row['posterior'])} al año.",
        ]
        if weight < 0.0005:
            parts.append(
                "Con el objetivo de máximo Sharpe el modelo no lo incluye: otros activos ofrecen más retorno "
                "esperado por cada unidad de riesgo que agregan."
            )
        elif abs(tilt) < 0.005:
            parts.append(
                f"El modelo le asigna {_pct(weight)}, casi su peso de mercado: sin opiniones distintas del "
                "equilibrio, el portafolio de mercado ya es el de mejor retorno esperado por unidad de riesgo."
            )
        elif tilt > 0:
            parts.append(
                f"El modelo le asigna {_pct(weight)} ({_points(tilt)} frente al mercado): su retorno esperado "
                "compensa bien su riesgo o ayuda a diversificar."
            )
        else:
            parts.append(
                f"El modelo le asigna {_pct(weight)} ({_points(tilt)} frente al mercado): su retorno esperado "
                "no compensa tanto el riesgo que agrega frente a los demás."
            )
        rows.append({**row, "text": " ".join(parts)})
    summary = (
        "Black-Litterman parte del equilibrio de mercado: los retornos que justifican los pesos actuales "
        "del mercado según el tamaño de cada activo. Luego mezcla ese punto de partida con las views "
        "(opiniones sobre cada activo). En esta vista guiada usamos views neutrales, iguales al retorno "
        "de equilibrio, así que el resultado refleja solo al mercado y al objetivo de máximo Sharpe."
    )
    return {"rows": rows, "summary": summary}


def neutral_views(result: dict) -> dict[str, float]:
    """Equilibrium returns of a first Black-Litterman run, as annual views (decimals)."""
    return {ticker: value / 100 for ticker, value in (result.get("prior_returns") or {}).items()}


# Glossary ------------------------------------------------------------------------
GLOSSARY: dict[str, str] = {
    "Activo": "Algo en lo que se puede invertir: una acción, un fondo (ETF), una criptomoneda o una materia prima.",
    "Portafolio": "El conjunto de activos en los que repartes tu dinero, con el porcentaje de cada uno.",
    "Peso": "La parte del portafolio que va a un activo. Si un activo pesa 25 %, de cada 100 pesos invertidos 25 van a él.",
    "Diversificación": "Repartir el dinero entre activos que no se mueven igual, para que una caída de uno no arrastre todo el portafolio.",
    "Volatilidad": "Cuánto suben y bajan los precios. Más volatilidad significa movimientos más grandes, hacia arriba y hacia abajo.",
    "Correlación": "Qué tan parecido se mueven dos activos, de −1 (opuestos) a +1 (iguales). Una correlación baja ayuda a diversificar.",
    "Retorno": "Cuánto ganó o perdió una inversión en un periodo, en porcentaje.",
    "Drawdown (caída máxima)": "La mayor caída desde un máximo hasta un mínimo posterior. Mide el peor tramo que habrías vivido.",
    "Ratio de Sharpe": "Retorno por encima de una tasa sin riesgo dividido entre la volatilidad. Más alto = más retorno por cada unidad de riesgo.",
    "Ratio de Sortino": "Como el Sharpe, pero solo cuenta la volatilidad de las caídas.",
    "Benchmark": "Una referencia para comparar, por ejemplo el índice S&P 500 (SPY).",
    "Rebalanceo": "Volver a ajustar los pesos a los objetivos, porque con el tiempo los activos que suben ganan peso y los que bajan lo pierden.",
    "Capitalización bursátil": "El valor total de una empresa en bolsa (precio por número de acciones). Black-Litterman la usa como punto de partida.",
    "Equilibrio de mercado": "Los retornos esperados que hacen razonables los pesos actuales del mercado. Es el punto de partida de Black-Litterman.",
    "View": "Una opinión sobre el retorno anual de un activo. Black-Litterman la mezcla con el equilibrio según la confianza que tenga.",
    "HRP (Hierarchical Risk Parity)": "Método que agrupa los activos por su correlación y reparte el capital para equilibrar el riesgo de cada grupo, sin predecir retornos.",
    "Black-Litterman": "Método que parte del equilibrio de mercado, lo ajusta con views y busca el mejor portafolio para un objetivo (aquí, máximo Sharpe).",
    "Perfil de riesgo": "Tu disposición y capacidad para asumir pérdidas, según el cuestionario de Atlas.",
    "Horizonte": "El tiempo que piensas mantener la inversión. Horizontes cortos dejan menos tiempo para recuperarse de una caída.",
}
GUIDED_TERMS = (
    "Peso",
    "Diversificación",
    "Volatilidad",
    "Correlación",
    "Drawdown (caída máxima)",
    "Ratio de Sharpe",
    "Rebalanceo",
    "Benchmark",
    "HRP (Hierarchical Risk Parity)",
)
# Only the gestor has Black-Litterman; nobody else needs these terms.
GESTOR_TERMS = frozenset({"Capitalización bursátil", "Equilibrio de mercado", "View", "Black-Litterman"})


def glossary(terms=None) -> list[tuple[str, str]]:
    return [(term, GLOSSARY[term]) for term in (terms or GLOSSARY)]
