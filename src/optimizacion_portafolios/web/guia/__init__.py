"""Broker guide and options for each diagnosis category (content in ``config/``)."""

from flask import Blueprint

from optimizacion_portafolios.web.guia.content import load_guide, load_instruments

bp = Blueprint("guia", __name__, url_prefix="/guia", template_folder="templates")


@bp.record_once
def _validate_guide(state) -> None:
    """Fail at start-up (with the validation message) if a YAML file is malformed."""
    load_guide(state.app.config.get("GUIA_IBKR_PATH"))
    load_instruments(state.app.config.get("INSTRUMENTOS_PATH"))


from optimizacion_portafolios.web.guia import routes  # noqa: E402,F401
