"""Portfolio optimization opens from a capital in dollars, at the day's rate.

Saved profiles of earlier versions only have a range: a range that crosses the
threshold cannot be decided, so that person is asked to update the amount.
Without any dollar rate nobody is stopped.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from flask import current_app

from optimizacion_portafolios.data import trm
from optimizacion_portafolios.data.trm import DollarRate
from optimizacion_portafolios.profiling.questionnaire import CAPITAL_AMOUNT_FIELD
from optimizacion_portafolios.storage.models import Profile
from optimizacion_portafolios.web.guia.content import load_instruments
from optimizacion_portafolios.web.profile.access import get_questionnaire


@dataclass(frozen=True)
class OptimizationAccess:
    unlocked: bool
    threshold_usd: int
    rate: DollarRate | None = None
    threshold_cop: float | None = None
    amount: int | None = None
    missing_usd: int | None = None
    needs_update: bool = False


def format_cop(value: float) -> str:
    """``3221190.4`` -> ``"$3.221.190"``."""
    return "$" + f"{value:,.0f}".replace(",", ".")


def dollar_rate() -> DollarRate | None:
    """The day's rate; tests set ``DOLLAR_RATE`` so they never reach the network."""
    if "DOLLAR_RATE" in current_app.config:
        return current_app.config["DOLLAR_RATE"]
    return trm.current_rate()


def optimization_text() -> dict:
    return load_instruments(current_app.config.get("INSTRUMENTOS_PATH"))["optimizacion"]


def optimization_access(profile: Profile) -> OptimizationAccess:
    threshold_usd = optimization_text()["umbral_usd"]
    rate = dollar_rate()
    if rate is None:
        return OptimizationAccess(True, threshold_usd)
    threshold = threshold_usd * rate.value
    base = {"threshold_usd": threshold_usd, "rate": rate, "threshold_cop": threshold}
    amount = (profile.respuestas or {}).get(CAPITAL_AMOUNT_FIELD)
    if amount is None:
        try:
            low, high = get_questionnaire().capital_bounds(profile.capital_rango)
        except KeyError:
            return OptimizationAccess(False, needs_update=True, **base)
        if low >= threshold:
            return OptimizationAccess(True, **base)
        if high is not None and high <= threshold:
            return OptimizationAccess(False, missing_usd=ceil((threshold - high) / rate.value), **base)
        return OptimizationAccess(False, needs_update=True, **base)
    if amount >= threshold:
        return OptimizationAccess(True, amount=amount, **base)
    return OptimizationAccess(False, amount=amount, missing_usd=ceil((threshold - amount) / rate.value), **base)


def capital_in_dollars(profile: Profile | None) -> float | None:
    """The capital the person wrote, in dollars at the day's rate."""
    rate = dollar_rate()
    amount = (profile.respuestas or {}).get(CAPITAL_AMOUNT_FIELD) if profile else None
    return amount / rate.value if rate and amount else None


def access_view(access: OptimizationAccess) -> dict:
    """Texts of the explanation, with today's figures."""
    text = optimization_text()
    values = {"usd": f"{access.threshold_usd:,}".replace(",", ".")}
    view = {
        "title": text["titulo"].format_map(values),
        "reason": text["motivo"],
        "amount": format_cop(access.amount) if access.amount is not None else None,
        "missing_usd": access.missing_usd,
        "needs_update": access.needs_update,
        "threshold_usd": values["usd"],
    }
    if access.rate is not None:
        view["since"] = text["habilitada_desde"].format_map(
            {
                **values,
                "cop": format_cop(access.threshold_cop),
                "fuente": access.rate.source,
                "fecha": access.rate.day,
            }
        )
    return view
