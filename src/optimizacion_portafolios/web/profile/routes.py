"""Questionnaire wizard, profile result and "Mi perfil".

The wizard has one step per block of the questionnaire. Every submitted step is stored in the user's
draft (``perfil_borradores``), so an unfinished questionnaire resumes where it
was left; completing the last step saves a new current profile and assigns the
role from the diagnosis (the gestor keeps theirs).
"""

from __future__ import annotations

import logging
import re

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from optimizacion_portafolios.profiling import (
    PRIOR_FIELDS,
    VETO_FACTOR,
    AnswerError,
    Questionnaire,
    clean_answers,
    evaluate,
    explain,
    format_points,
    format_score,
)
from optimizacion_portafolios.profiling.questionnaire import CAPITAL_AMOUNT_FIELD
from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import Profile
from optimizacion_portafolios.storage.profiles import (
    delete_draft,
    get_current_profile,
    get_draft,
    list_profiles,
    save_draft,
    save_profile,
)
from optimizacion_portafolios.web.auth.roles import GESTOR_ROLE, canonical_role
from optimizacion_portafolios.web.guia.journey import build_journey
from optimizacion_portafolios.web.guia.threshold import format_cop
from optimizacion_portafolios.web.profile import bp
from optimizacion_portafolios.web.profile.access import get_questionnaire, refresh_profile_session

logger = logging.getLogger(__name__)

DISCLAIMER = "Información educativa. No es recomendación de inversión ni asesoría financiera."
MAX_AMOUNT_DIGITS = 14
ROLE_LABELS = {"novato": "Novato", "inversionista": "Inversionista", "gestor": "Gestor"}


# Draft helpers -------------------------------------------------------------------
def _load_draft(questionnaire: Questionnaire) -> tuple[dict, int]:
    """``(data, reached step)`` of the user's draft; drafts of other versions are discarded."""
    draft = get_draft(current_user.id)
    if draft is not None and draft.version_cuestionario != questionnaire.version:
        delete_draft(current_user.id)
        draft = None
    if draft is None:
        return {"respuestas": {}}, 0
    data = dict(draft.datos or {})
    data["respuestas"] = clean_answers(questionnaire, data.get("respuestas") or {})
    return data, max(0, min(draft.paso, len(questionnaire.steps) - 1))


def _parse_amount(raw) -> int | None:
    """Pesos typed by the user (``3.500.000`` or ``$3500000``); ``None`` if not valid."""
    digits = re.sub(r"\D", "", raw or "")
    amount = int(digits) if digits and len(digits) <= MAX_AMOUNT_DIGITS else 0
    return amount or None


def _step_questions(questionnaire: Questionnaire, step: int):
    return questionnaire.block(questionnaire.steps[step]).questions


def _step_prior_fields(questionnaire: Questionnaire, step: int) -> list[str]:
    layout = questionnaire.block(questionnaire.steps[step]).layout
    return [field for field in layout if field in PRIOR_FIELDS]


def _merge_step(questionnaire: Questionnaire, step: int, data: dict, form) -> list[str]:
    """Copy the valid answers of ``step`` from ``form`` into ``data``; return missing fields."""
    prior = _step_prior_fields(questionnaire, step)
    if "capital_rango" in prior:
        amount = _parse_amount(form.get(CAPITAL_AMOUNT_FIELD))
        if amount is not None:
            data[CAPITAL_AMOUNT_FIELD] = amount
            data["capital_rango"] = questionnaire.capital_range_for(amount)
    if "horizonte" in prior:
        value = form.get("horizonte", "")
        try:
            questionnaire.horizon(value)
        except KeyError:
            pass
        else:
            data["horizonte"] = value
    questions = _step_questions(questionnaire, step)
    submitted = {question.id: form.get(question.id) for question in questions if question.id in form}
    data["respuestas"].update(clean_answers(questionnaire, submitted))
    missing = [CAPITAL_AMOUNT_FIELD if name == "capital_rango" else name for name in prior if not data.get(name)]
    return missing + [question.id for question in questions if question.id not in data["respuestas"]]


def _first_incomplete_step(questionnaire: Questionnaire, data: dict) -> int | None:
    for step in range(len(questionnaire.steps)):
        if any(not data.get(name) for name in _step_prior_fields(questionnaire, step)):
            return step
        if any(question.id not in data["respuestas"] for question in _step_questions(questionnaire, step)):
            return step
    return None


def _field(questionnaire: Questionnaire, name: str, number: int, data: dict) -> dict:
    """Template data of one field of the wizard."""
    if name == "capital_rango":
        return {
            "name": CAPITAL_AMOUNT_FIELD,
            "legend": f"{number}. {questionnaire.capital_text}",
            "amount": True,
            "selected": data.get(CAPITAL_AMOUNT_FIELD),
            "help": "Escribe el monto en pesos colombianos (COP), por ejemplo 3500000.",
        }
    if name == "horizonte":
        legend, options = questionnaire.horizon_text, questionnaire.horizon_options
    else:
        question = questionnaire.question(name)
        return {
            "name": name,
            "legend": f"{number}. {question.text}",
            "options": list(enumerate(question.options, start=1)),
            "selected": data["respuestas"].get(name),
            "help": None,
        }
    return {
        "name": name,
        "legend": f"{number}. {legend}",
        "options": [(choice.id, choice.text) for choice in options],
        "selected": data.get(name),
        "help": None,
    }


def _render_step(questionnaire: Questionnaire, step: int, data: dict, reached: int, missing=(), status=200):
    block = questionnaire.block(questionnaire.steps[step])
    # Questions are numbered across blocks (1 to 8 with the shipped file).
    offset = sum(len(other.layout) for other in questionnaire.blocks[:step])
    fields = [
        _field(questionnaire, name, number, data)
        for number, name in enumerate(block.layout, start=offset + 1)
    ]
    return render_template(
        "profile/questionnaire.html",
        step=step,
        intro=block.description,
        fields=fields,
        titles=[other.title for other in questionnaire.blocks],
        reached=reached,
        data=data,
        missing=set(missing),
        repeating=get_current_profile(current_user.id) is not None,
        is_last=step == len(questionnaire.steps) - 1,
    ), status


# Questionnaire -----------------------------------------------------------------
@bp.get("/cuestionario")
def questionnaire():
    questionnaire = get_questionnaire()
    data, reached = _load_draft(questionnaire)
    step = request.args.get("paso", type=int)
    if step is None or not 0 <= step <= reached:
        step = reached
    return _render_step(questionnaire, step, data, reached)


@bp.post("/cuestionario")
def save_step():
    questionnaire = get_questionnaire()
    data, reached = _load_draft(questionnaire)
    step = request.form.get("paso", type=int)
    if step is None or not 0 <= step <= reached:
        return redirect(url_for("profile.questionnaire"))
    missing = _merge_step(questionnaire, step, data, request.form)

    if request.form.get("accion") == "anterior":
        save_draft(current_user.id, questionnaire.version, reached, data)
        return redirect(url_for("profile.questionnaire", paso=max(0, step - 1)))
    if missing:
        save_draft(current_user.id, questionnaire.version, reached, data)
        flash("Responde todas las preguntas de este paso para continuar.", "error")
        return _render_step(questionnaire, step, data, reached, missing, status=400)

    last = len(questionnaire.steps) - 1
    if step < last:
        save_draft(current_user.id, questionnaire.version, max(reached, step + 1), data)
        return redirect(url_for("profile.questionnaire", paso=step + 1))

    pending = _first_incomplete_step(questionnaire, data)
    if pending is not None:
        save_draft(current_user.id, questionnaire.version, pending, data)
        flash("Faltan respuestas en un paso anterior.", "error")
        return redirect(url_for("profile.questionnaire", paso=pending))
    try:
        result = evaluate(questionnaire, data["capital_rango"], data["horizonte"], data["respuestas"])
    except AnswerError as error:  # defensive: the steps were validated above
        flash(str(error), "error")
        return redirect(url_for("profile.questionnaire", paso=0))
    try:
        profile = save_profile(current_user.id, result, capital_amount=data.get(CAPITAL_AMOUNT_FIELD))
        if not _is_gestor():
            current_user.role = result.suggested_role
            current_user.role_confirmed = True
            db.session.commit()
    except IntegrityError:
        # Two submissions at once: the other one already stored the profile.
        refresh_profile_session(current_user)
        flash("Tu perfil ya se había guardado en otra pestaña o envío.", "info")
        return redirect(url_for("profile.result"))
    refresh_profile_session(current_user)
    logger.info("Risk profile %s saved for user %s (%s)", profile.id, current_user.id, result.final_level)
    flash("Tu perfil de riesgo quedó guardado.", "success")
    return redirect(url_for("profile.result"))


# Result, role and "Mi perfil" ------------------------------------------------------
def _emergency_fund_text(questionnaire: Questionnaire, profile: Profile) -> str | None:
    question = questionnaire.question(questionnaire.capacity_question)
    answer = (profile.respuestas or {}).get(question.id)
    return question.options[answer - 1] if answer else None


def _profile_view(questionnaire: Questionnaire, profile: Profile) -> dict:
    """Labels and the plain-language explanation of a saved profile."""
    explanation = None
    if profile.version_cuestionario == questionnaire.version:
        try:
            result = evaluate(questionnaire, profile.capital_rango, profile.horizonte, profile.respuestas or {})
            explanation = explain(questionnaire, result)
        except (AnswerError, KeyError):
            explanation = None

    def label(level_id):
        try:
            return questionnaire.level(level_id).label
        except KeyError:
            return level_id

    def choice_text(lookup, choice_id):
        try:
            return lookup(choice_id)
        except KeyError:
            return choice_id

    current = profile.version_cuestionario == questionnaire.version
    format_value = format_points if current else format_score

    try:
        description = questionnaire.level(profile.perfil_final).description
    except KeyError:
        description = ""
    horizon_capped = profile.nivel_horizonte != questionnaire.levels[-1].id
    return {
        "profile": profile,
        "final_label": label(profile.perfil_final),
        "final_description": description,
        "factor_label": questionnaire.factor_labels.get(profile.factor_limitante, profile.factor_limitante),
        "is_veto": profile.factor_limitante == VETO_FACTOR,
        "explanation": explanation
        or f"Perfil calculado con la versión {profile.version_cuestionario} del cuestionario.",
        # Up to version 1: averages from 1 to 4 of tolerance and capacity.
        "current_scale": current,
        "score_max": questionnaire.max_score if current else 4,
        "tolerance": (format_value(profile.puntaje_tolerancia), label(profile.nivel_tolerancia)),
        "capacity": (format_value(profile.puntaje_capacidad), label(profile.nivel_capacidad)),
        "emergency_fund": _emergency_fund_text(questionnaire, profile) if current else None,
        "horizon": (
            choice_text(questionnaire.horizon_text_of, profile.horizonte),
            f"Máximo {label(profile.nivel_horizonte).lower()}" if horizon_capped else "Sin tope",
        ),
        "capital": (
            format_cop(amount) if (amount := (profile.respuestas or {}).get(CAPITAL_AMOUNT_FIELD))
            else choice_text(questionnaire.capital_text_of, profile.capital_rango)
        ),
        "suggested_role": canonical_role(profile.rol_sugerido),
    }


def _is_gestor() -> bool:
    return current_user.role == GESTOR_ROLE


@bp.get("/resultado")
def result():
    profile = get_current_profile(current_user.id)
    if profile is None:
        return redirect(url_for("profile.questionnaire"))
    return render_template(
        "profile/result.html",
        view=_profile_view(get_questionnaire(), profile),
        journey=None if _is_gestor() else build_journey(profile),
        disclaimer=DISCLAIMER,
    )


@bp.get("/")
def mine():
    profile = get_current_profile(current_user.id)
    if profile is None:
        return redirect(url_for("profile.questionnaire"))
    questionnaire = get_questionnaire()
    history = list_profiles(current_user.id)
    journey = None if _is_gestor() else build_journey(profile)
    labels = {level.id: level.label for level in questionnaire.levels}
    return render_template(
        "profile/mine.html",
        view=_profile_view(questionnaire, profile),
        journey=journey,
        disclaimer=DISCLAIMER,
        history=history,
        level_labels=labels,
        factor_labels=questionnaire.factor_labels,
        draft=get_draft(current_user.id),
        role_label=ROLE_LABELS.get(current_user.role, current_user.role),
        is_gestor=_is_gestor(),
    )


@bp.post("/repetir")
def repeat():
    """Start the questionnaire again; the current profile stays until it is completed."""
    delete_draft(current_user.id)
    return redirect(url_for("profile.questionnaire"))

