"""First steps of a new user: data notice and introduction to the app."""

from flask import Blueprint

bp = Blueprint("onboarding", __name__, template_folder="templates")

from optimizacion_portafolios.web.onboarding import routes  # noqa: E402,F401
