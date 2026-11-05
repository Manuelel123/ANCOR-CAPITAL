"""Guide page: the capital of the current profile changes the message, never the access."""

from flask import current_app, render_template

from optimizacion_portafolios.web.guia import bp
from optimizacion_portafolios.web.guia.content import load_guide
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
