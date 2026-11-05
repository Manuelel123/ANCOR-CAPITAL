"""Educational guide to open a broker account (content in ``config/guia_ibkr.yaml``)."""

from flask import Blueprint

from optimizacion_portafolios.web.guia.content import load_guide

bp = Blueprint("guia", __name__, url_prefix="/guia", template_folder="templates")


@bp.record_once
def _validate_guide(state) -> None:
    """Fail at start-up (with the validation message) if the YAML is malformed."""
    load_guide(state.app.config.get("GUIA_IBKR_PATH"))


from optimizacion_portafolios.web.guia import routes  # noqa: E402,F401
