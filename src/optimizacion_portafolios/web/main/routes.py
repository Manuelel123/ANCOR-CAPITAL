"""Portada (signed out), main screen (signed in) and glossary."""

from flask import redirect, render_template, url_for
from flask_login import current_user

from optimizacion_portafolios.storage.profiles import get_current_profile

from optimizacion_portafolios.data.catalogs import (
    ACTIVOS_HRP,
    ACTIVOS_OPCIONES,
    BENCHMARKS,
)
from optimizacion_portafolios.web.auth.permissions import (
    can_open,
)
from optimizacion_portafolios.web.auth.passwords import password_rules
from optimizacion_portafolios.web.auth.roles import GESTOR_ROLE
from optimizacion_portafolios.web.common.guided import GESTOR_TERMS, GLOSSARY
from optimizacion_portafolios.web.common.guided import glossary as glossary_items
from optimizacion_portafolios.web.guia.journey import build_journey
from optimizacion_portafolios.web.main import bp
from optimizacion_portafolios.web.portfolios.services import public_portfolios
from optimizacion_portafolios.web.profile.access import (
    current_profile,
    pending_onboarding_step,
    pending_profile_step,
)

MODEL_COUNT = 2
DATA_SOURCE_COUNT = 2

# Modules of the original home page, shown to the gestor ("modo original").
GESTOR_MODULES = (
    {
        "key": "market",
        "endpoint": "monitoring.monitoring",
        "eyebrow": "Mercado",
        "title": "Monitoreo de mercado",
        "description": "Precios, retornos, volatilidad y fundamentales de cada activo en un solo lugar.",
        "features": (
            "Gráfico de precios interactivo con rangos",
            "Volatilidad histórica por periodo",
            "Perfil fundamental de cada compañía",
        ),
    },
    {
        "key": "hrp",
        "endpoint": "hrp.hrp",
        "eyebrow": "Riesgo jerárquico",
        "title": "Optimización HRP",
        "description": "Reparte el riesgo por clusters de correlación sin estimar retornos esperados.",
        "features": (
            "Clustering por correlación y dendrograma",
            "Límites de peso globales y por activo",
            "Desempeño histórico frente a un benchmark",
        ),
    },
    {
        "key": "black_litterman",
        "endpoint": "black_litterman.black_litterman",
        "eyebrow": "Equilibrio + views",
        "title": "Black-Litterman",
        "description": "Combina el equilibrio implícito del mercado con tus expectativas por activo.",
        "features": (
            "Prior de equilibrio por capitalización",
            "Views anuales con confianza de Idzorek",
            "Objetivos de Sharpe, Sortino, CVaR y utilidad",
        ),
    },
)

# Tools of the main screen; each card shows only if the role can open it.
TOOLS = (
    {
        "key": "hrp",
        "endpoint": "hrp.hrp",
        "eyebrow": "Riesgo jerárquico",
        "title": "Optimización HRP",
        "description": "Reparte el dinero según el riesgo y la correlación de los activos, sin predecir retornos.",
    },
    {
        "key": "black_litterman",
        "endpoint": "black_litterman.black_litterman",
        "eyebrow": "Equilibrio + views",
        "title": "Black-Litterman",
        "description": "Parte del equilibrio de mercado y busca el mejor retorno esperado por unidad de riesgo.",
    },
    {
        "key": "compare",
        "endpoint": "compare.compare",
        "eyebrow": "Dos métodos",
        "title": "Comparar HRP y Black-Litterman",
        "description": "Los dos portafolios con los mismos activos, lado a lado, con sus métricas clave.",
    },
    {
        "key": "market",
        "endpoint": "monitoring.monitoring",
        "eyebrow": "Mercado",
        "title": "Monitoreo de mercado",
        "description": "Precios, retornos, volatilidad, opciones y fundamentales de cada activo.",
    },
    {
        "key": "portfolios",
        "endpoint": "portfolios.index",
        "eyebrow": "Seguimiento",
        "title": "Mis portafolios",
        "description": "Los portafolios que fijaste: panel, alertas y versiones.",
    },
    {
        "key": "history",
        "endpoint": "decisions.history",
        "eyebrow": "Registro",
        "title": "Mi historial",
        "description": "Cada portafolio que generaste, con fecha, perfil vigente, método, pesos y supuestos.",
    },
    {
        "key": "options",
        "endpoint": "guia.options",
        "eyebrow": "Para ti",
        "title": "Opciones para tu perfil",
        "description": "Ejemplos de instrumentos según tu categoría y tu capital.",
    },
    {
        "key": "guide",
        "endpoint": "guia.interactive_brokers",
        "eyebrow": "Guía",
        "title": "Abrir cuenta en Interactive Brokers",
        "description": "El broker de referencia, paso a paso: qué hacer, por qué importa y qué falta confirmar.",
    },
    {
        "key": "glossary",
        "endpoint": "main.glossary",
        "eyebrow": "Aprende",
        "title": "Glosario",
        "description": "Diversificación, volatilidad, correlación, drawdown, Sharpe y más, en lenguaje simple.",
    },
)


def catalog_asset_count() -> int:
    """Unique tickers offered across the asset, benchmark and options catalogs."""
    tickers = (
        set(ACTIVOS_HRP.values())
        | set(BENCHMARKS.values())
        | set(ACTIVOS_OPCIONES.values())
    )
    return len(tickers)


@bp.get("/")
def index():
    if not current_user.is_authenticated:
        stats = (
            {"value": catalog_asset_count(), "label": "activos e índices en catálogo"},
            {"value": 1, "label": "método de construcción de portafolios"},
            {"value": DATA_SOURCE_COUNT, "label": "fuentes de datos de mercado"},
        )
        return render_template(
            "main/index.html",
            stats=stats,
            password_rules=password_rules(),
        )
    # The home page is public, so the access checks do not run here.
    onboarding_step = pending_onboarding_step(current_user)
    if onboarding_step:
        return redirect(url_for(onboarding_step))
    if current_user.role == GESTOR_ROLE:
        # The gestor uses the app exactly as before the educational layer.
        stats = (
            {"value": catalog_asset_count(), "label": "activos e índices en catálogo"},
            {"value": MODEL_COUNT, "label": "modelos de optimización"},
            {"value": DATA_SOURCE_COUNT, "label": "fuentes de datos de mercado"},
        )
        return render_template("main/original_index.html", modules=GESTOR_MODULES, stats=stats)
    # A user without a profile (or role) sees the pending step instead of the tools.
    step = pending_profile_step(current_user)
    tools = [] if step else [tool for tool in TOOLS if can_open(tool["endpoint"])]
    saved_profile = get_current_profile(current_user.id)
    journey = build_journey(saved_profile) if saved_profile else None
    if journey and journey.has_ibkr:  # the card is for opening the account
        tools = [tool for tool in tools if tool["key"] != "guide"]
    return render_template(
        "main/dashboard.html",
        profile=current_profile(),
        journey=journey,
        gestor_portfolios=public_portfolios(),
        pending_step=step,
        tools=tools,
    )


@bp.get("/glosario")
def glossary():
    terms = [term for term in GLOSSARY if can_open("black_litterman.black_litterman") or term not in GESTOR_TERMS]
    return render_template("main/glossary.html", items=glossary_items(terms))
