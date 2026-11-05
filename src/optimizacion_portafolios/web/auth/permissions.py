"""What each role may open and do: one table, one access check.

Roles are tiered (``storage.models.ROLES``): ``novato`` < ``inversionista``
< ``gestor``. Two tables decide everything:

- ``ENDPOINT_ROLES``: endpoint -> roles that may open it. ``METHOD_ROLES``
  narrows one method of an endpoint (e.g. viewing alerts is open to every
  role, saving thresholds is not). Endpoints missing from the table are
  ``gestor``-only (deny by default), so a new page never opens by accident.
- ``FEATURE_ROLES``: in-page capabilities (advanced parameters, weight limits,
  fixing a portfolio...). Templates read them through ``can(feature)`` and
  the routes check the same function, so a hidden control cannot be used by
  posting the form directly.

``require_role`` is registered with ``guard.register_access_check`` after the
profile check: a signed-in user whose role does not reach the endpoint is
redirected to the home page with a message (403 JSON for ``/api/*``).
"""

from __future__ import annotations

from flask import Flask, flash, jsonify, redirect, request, url_for
from flask_login import current_user

from optimizacion_portafolios.web.auth import guard
from optimizacion_portafolios.web.auth.roles import canonical_role
from optimizacion_portafolios.web.auth.session import wants_json

NOVATO = "novato"
INVERSIONISTA = "inversionista"
GESTOR = "gestor"

EVERYONE = frozenset({NOVATO, INVERSIONISTA, GESTOR})
INVESTORS = frozenset({INVERSIONISTA, GESTOR})
MANAGERS = frozenset({GESTOR})

ROLE_LABELS = {NOVATO: "Novato", INVERSIONISTA: "Inversionista", GESTOR: "Gestor"}
ROLE_SUMMARIES = {
    NOVATO: "HRP y Black-Litterman guiados, con explicaciones y glosario. Mis portafolios en solo lectura.",
    INVERSIONISTA: "Lo del novato, más límites de peso, excluir activos, comparar HRP con Black-Litterman, fijar y gestionar portafolios.",
    GESTOR: "Acceso total: todos los parámetros, Mercado y Opciones, backtests, señales y gestión.",
}

# Endpoint -> roles allowed. Profiling pages (``profile.*``) are open to every
# signed-in role; public endpoints never reach the check.
ENDPOINT_ROLES: dict[str, frozenset[str]] = {
    "main.index": EVERYONE,
    "main.glossary": EVERYONE,
    "onboarding.data_notice": EVERYONE,
    "onboarding.introduction": EVERYONE,
    "guia.interactive_brokers": EVERYONE,
    "decisions.history": EVERYONE,
    "hrp.hrp": EVERYONE,
    "black_litterman.black_litterman": EVERYONE,
    "compare.compare": INVESTORS,
    "monitoring.monitoring": MANAGERS,
    # JSON for the price explorer, shown only on the gestor's full pages.
    "history.asset_history": MANAGERS,
    "history.asset_ohlc": MANAGERS,
    "portfolios.index": EVERYONE,
    "portfolios.detail": EVERYONE,
    "portfolios.alerts": EVERYONE,
    "portfolios.versions": EVERYONE,
    "portfolios.fix": INVESTORS,
    "portfolios.manage": INVESTORS,
    "portfolios.preview": INVESTORS,
    "portfolios.confirm": INVESTORS,
    "portfolios.archive": INVESTORS,
    "portfolios.signals": MANAGERS,
    "account.backtests": MANAGERS,
    "account.backtest_detail": MANAGERS,
    "account.delete_backtest": MANAGERS,
    "auth.logout": EVERYONE,
}
# (endpoint, method) -> roles, overriding ENDPOINT_ROLES for that method.
METHOD_ROLES: dict[tuple[str, str], frozenset[str]] = {
    ("portfolios.alerts", "POST"): INVESTORS,
}
OPEN_PREFIXES = ("profile.",)

# In-page capabilities.
FEATURE_ROLES: dict[str, frozenset[str]] = {
    # Every parameter of the HRP/BL pages (dates, benchmark, views, objective,
    # rate, custom tickers, per-asset limits, report download, price explorer).
    "advanced_params": MANAGERS,
    "backtest": MANAGERS,
    "market": MANAGERS,
    "signals": MANAGERS,
    "backtest_history": MANAGERS,
    # Guided pages: choose the assets (exclude some) and a maximum weight.
    "weight_limits": INVESTORS,
    "exclude_assets": INVESTORS,
    "compare": INVESTORS,
    "fix_portfolio": INVESTORS,
    "manage_portfolio": INVESTORS,
    "edit_alerts": INVESTORS,
}

DENIED_MESSAGE = "Tu rol ({role}) no incluye esta función."


def user_role(user=None) -> str | None:
    user = current_user if user is None else user
    if not getattr(user, "is_authenticated", False):
        return None
    return user.role


def role_label(role: str | None) -> str:
    """Label of a role, including legacy names of historical rows."""
    role = canonical_role(role) or ""
    return ROLE_LABELS.get(role, role)


def allowed_roles(endpoint: str, method: str = "GET") -> frozenset[str]:
    if endpoint.startswith(OPEN_PREFIXES):
        return EVERYONE
    override = METHOD_ROLES.get((endpoint, method))
    if override is not None:
        return override
    return ENDPOINT_ROLES.get(endpoint, MANAGERS)


def role_allows(role: str | None, endpoint: str, method: str = "GET") -> bool:
    return role is not None and role in allowed_roles(endpoint, method)


def can(feature: str, role: str | None = None) -> bool:
    """Whether ``role`` (default: the signed-in user's) has ``feature``."""
    role = user_role() if role is None else role
    return role is not None and role in FEATURE_ROLES.get(feature, MANAGERS)


def can_open(endpoint: str, role: str | None = None) -> bool:
    """Whether ``role`` (default: the signed-in user's) may GET ``endpoint``."""
    return role_allows(user_role() if role is None else role, endpoint)


def denied_response(message: str | None = None):
    message = message or DENIED_MESSAGE.format(role=role_label(user_role()))
    if wants_json():
        return jsonify({"error": message}), 403
    flash(message, "warning")
    return redirect(url_for("main.index"))


def require_role(endpoint: str):
    """Access check: the signed-in user's role must reach ``endpoint``."""
    if role_allows(user_role(), endpoint, request.method):
        return None
    return denied_response()


# Navigation (blueprint, endpoint, label), filtered by role in the template.
NAV_ITEMS = (
    ("main", "main.index", "Inicio"),
    ("monitoring", "monitoring.monitoring", "Mercado"),
    ("hrp", "hrp.hrp", "HRP"),
    ("black_litterman", "black_litterman.black_litterman", "Black-Litterman"),
    ("compare", "compare.compare", "Comparar"),
    ("portfolios", "portfolios.index", "Mis portafolios"),
)


# Pages added with the educational layer; the gestor's menu leaves them out
# so it matches the original app exactly ("modo original").
EDUCATIONAL_NAV = frozenset({"compare.compare"})


def original_mode() -> bool:
    """True for the gestor: the app looks and works exactly as before."""
    return user_role() == GESTOR


def nav_items(role: str | None) -> list[tuple[str, str, str]]:
    if role is None:
        return []
    items = [item for item in NAV_ITEMS if role_allows(role, item[1])]
    if role == GESTOR:
        items = [item for item in items if item[1] not in EDUCATIONAL_NAV]
    return items


def init_app(app: Flask) -> None:
    guard.register_access_check(app, require_role)
    # Globals, so macros imported without context can use them too.
    app.jinja_env.globals.update(can=can, can_open=can_open, original_mode=original_mode)

    @app.context_processor
    def inject_permissions():
        role = user_role()
        return {
            "nav_items": nav_items(role),
            "user_role_label": role_label(role),
        }
