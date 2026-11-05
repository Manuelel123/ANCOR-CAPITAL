"""What a user sees after the diagnosis: category, journey and options."""

from __future__ import annotations

from dataclasses import dataclass

from flask import current_app

from optimizacion_portafolios.profiling import already_invests, category_for, emergency_fund_complete
from optimizacion_portafolios.profiling.questionnaire import Category, Questionnaire
from optimizacion_portafolios.storage.models import Profile
from optimizacion_portafolios.web.guia.content import load_instruments
from optimizacion_portafolios.web.profile.access import get_questionnaire

CONTRIBUTION_QUESTION = "aporte"


@dataclass(frozen=True)
class Journey:
    category: Category
    invests: bool
    capital_level: str | None
    fund_complete: bool
    steps: tuple[dict, ...]
    contribution: str | None


def _capital_level(questionnaire: Questionnaire, profile: Profile) -> str | None:
    try:
        return questionnaire.capital_level(profile.capital_rango)
    except KeyError:
        return None


def _contribution(questionnaire: Questionnaire, profile: Profile) -> str | None:
    answer = (profile.respuestas or {}).get(CONTRIBUTION_QUESTION)
    try:
        return questionnaire.question(CONTRIBUTION_QUESTION).options[answer - 1] if answer else None
    except (KeyError, IndexError):
        return None


def _highlights_hrp(questionnaire: Questionnaire, catalog: dict, capital_level: str | None) -> bool:
    if capital_level is None:
        return False
    levels = questionnaire.capital_levels
    return levels.index(capital_level) >= levels.index(catalog["hrp_destacado_desde"])


def build_journey(profile: Profile) -> Journey:
    """Category and steps of a saved profile (works for any questionnaire version)."""
    questionnaire = get_questionnaire()
    catalog = load_instruments(current_app.config.get("INSTRUMENTOS_PATH"))
    answers = profile.respuestas or {}
    category = category_for(questionnaire, profile.perfil_final, answers)
    invests = already_invests(questionnaire, answers)
    capital_level = _capital_level(questionnaire, profile)
    step_ids = list(catalog["recorrido"][category.id])
    if invests:
        step_ids.insert(0, "hrp_libre")
    elif _highlights_hrp(questionnaire, catalog, capital_level):
        step_ids.append("hrp_guiado")
    return Journey(
        category=category,
        invests=invests,
        capital_level=capital_level,
        fund_complete=emergency_fund_complete(questionnaire, profile.version_cuestionario, answers),
        steps=tuple(catalog["pasos"][step_id] for step_id in step_ids),
        contribution=_contribution(questionnaire, profile),
    )


def options_for(journey: Journey) -> list[dict]:
    """Instruments of the category; capital only filters by what the amount can open."""
    catalog = load_instruments(current_app.config.get("INSTRUMENTOS_PATH"))
    return [
        instrument
        for instrument in catalog["instrumentos"]
        if journey.category.id in instrument["categorias"]
        and (journey.capital_level is None or journey.capital_level in instrument["capital"])
        and (journey.fund_complete or instrument["riesgo_bajo"])
    ]
