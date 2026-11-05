"""Guide and options: the capital of the profile changes the message, never the access."""

from flask import current_app, redirect, render_template, url_for
from flask_login import current_user

from optimizacion_portafolios.storage.profiles import get_current_profile
from optimizacion_portafolios.web.guia import bp
from optimizacion_portafolios.web.guia.content import load_guide, load_instruments
from optimizacion_portafolios.web.guia.journey import build_journey, options_for
from optimizacion_portafolios.web.profile.access import current_profile, get_questionnaire

LOW_CAPITAL = "bajo"


def _capital_level() -> str | None:
    """Capital level of the current profile (``None`` without a profile)."""
    profile = current_profile()
    if profile is None:
        return None
    try:
        return get_questionnaire().capital_level(profile["capital_rango"])
    except KeyError:
        return None


@bp.get("/interactive-brokers")
def interactive_brokers():
    level = _capital_level()
    return render_template(
        "guia/interactive_brokers.html",
        guide=load_guide(current_app.config.get("GUIA_IBKR_PATH")),
        low_capital=level == LOW_CAPITAL,
        known_capital=level is not None,
    )


@bp.get("/opciones")
def options():
    profile = get_current_profile(current_user.id)
    if profile is None:
        return redirect(url_for("profile.questionnaire"))
    journey = build_journey(profile)
    return render_template(
        "guia/options.html",
        journey=journey,
        options=options_for(journey),
        catalog=load_instruments(current_app.config.get("INSTRUMENTOS_PATH")),
    )
