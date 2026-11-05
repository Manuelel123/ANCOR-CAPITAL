"""The current risk profile in the session, and the rule that every tool needs one.

``init_app`` wires three things:

- the access check ``require_current_profile`` (``web.auth.guard``): a
  signed-in user who has not accepted the current data notice is sent to it,
  then to the introduction the first time, one without a current profile to
  the questionnaire, and one whose role is still unconfirmed to the result
  page to confirm it. The ``gestor`` uses the original app: none of them.
  The profiling pages (``profile.*``) and the broker guide, linked from the
  result page, are exempt from the last two; sign-out, static files and the
  home page never reach the checks.
- an after-sign-in hook (``web.auth.session.register_after_login``) that loads
  the saved profile into ``session["perfil"]`` and, when the profile or the
  role is missing, redirects there instead of ``next``.
- the ``current_profile`` template variable (the same summary).

``session["perfil"]`` is a small JSON-serializable summary of the current
``perfiles`` row (see ``profile_summary``); it is refreshed on sign-in and when
the questionnaire is completed, and reloaded from the database if missing.
"""

from __future__ import annotations

from flask import Flask, current_app, flash, jsonify, redirect, session, url_for
from flask_login import current_user

from optimizacion_portafolios.profiling import Questionnaire, QuestionnaireError, load_questionnaire
from optimizacion_portafolios.profiling.avisos import DataNotice, load_notices
from optimizacion_portafolios.storage.models import Profile, User
from optimizacion_portafolios.storage.profiles import get_current_profile, is_current_profile
from optimizacion_portafolios.web.auth import guard
from optimizacion_portafolios.web.auth.roles import GESTOR_ROLE, canonical_role
from optimizacion_portafolios.web.auth.session import register_after_login, wants_json

PROFILE_SESSION_KEY = "perfil"
PROFILE_ENDPOINT_PREFIX = "profile."
QUESTIONNAIRE_MESSAGE = "Antes de usar las herramientas, completa tu perfil de riesgo."
ROLE_MESSAGE = "Confirma cómo quieres usar Atlas para continuar."
DATA_NOTICE_ENDPOINT = "onboarding.data_notice"
DATA_NOTICE_MESSAGE = "Antes de continuar, revisa y acepta el aviso de tratamiento de datos."
INTRODUCTION_ENDPOINT = "onboarding.introduction"
GUIDE_ENDPOINT = "guia.interactive_brokers"
_EXTENSION_KEY = "questionnaire_path"
_NOTICES_KEY = "avisos_path"


def get_questionnaire() -> Questionnaire:
    """The questionnaire of this app (reloaded when the YAML file changes)."""
    return load_questionnaire(current_app.extensions.get(_EXTENSION_KEY))


def get_data_notice() -> DataNotice:
    """The data notice of this app (reloaded when the YAML file changes)."""
    return load_notices(current_app.extensions.get(_NOTICES_KEY))


def needs_data_notice(user: User) -> bool:
    """True until ``user`` accepts the current version (the gestor never sees it)."""
    if user.role == GESTOR_ROLE:
        return False
    return user.datos_aceptados_at is None or user.datos_aviso_version != get_data_notice().version


def _label(questionnaire: Questionnaire | None, level_id: str) -> str:
    if questionnaire is not None:
        try:
            return questionnaire.level(level_id).label
        except KeyError:
            pass
    return level_id.capitalize()


def profile_summary(profile: Profile, questionnaire: Questionnaire | None = None) -> dict:
    """What ``session["perfil"]`` holds for ``profile``."""
    if questionnaire is None:
        try:
            questionnaire = get_questionnaire()
        except QuestionnaireError:
            questionnaire = None
    factor_labels = questionnaire.factor_labels if questionnaire else {}
    return {
        "id": profile.id,
        "usuario_id": profile.usuario_id,
        "fecha": profile.fecha.isoformat() if profile.fecha else None,
        "version_cuestionario": profile.version_cuestionario,
        "capital_rango": profile.capital_rango,
        "horizonte": profile.horizonte,
        "perfil_final": profile.perfil_final,
        "perfil_etiqueta": _label(questionnaire, profile.perfil_final),
        "factor_limitante": profile.factor_limitante,
        "factor_etiqueta": factor_labels.get(profile.factor_limitante, profile.factor_limitante),
        "nivel_tolerancia": profile.nivel_tolerancia,
        "nivel_capacidad": profile.nivel_capacidad,
        "nivel_horizonte": profile.nivel_horizonte,
        "puntaje_tolerancia": profile.puntaje_tolerancia,
        "puntaje_capacidad": profile.puntaje_capacidad,
        "rol_sugerido": canonical_role(profile.rol_sugerido),
    }


def refresh_profile_session(user: User) -> dict | None:
    """Load ``user``'s current profile into the session (or clear it)."""
    profile = get_current_profile(user.id)
    if profile is None:
        session.pop(PROFILE_SESSION_KEY, None)
        return None
    summary = profile_summary(profile)
    session[PROFILE_SESSION_KEY] = summary
    return summary


def session_profile(user: User) -> dict | None:
    """``user``'s profile summary from the session, loading it when missing."""
    summary = session.get(PROFILE_SESSION_KEY)
    if (
        isinstance(summary, dict)
        and summary.get("usuario_id") == user.id
        and is_current_profile(user.id, summary.get("id"))
    ):
        return summary
    # Missing, from another user, or replaced in another session/device.
    return refresh_profile_session(user)


def current_profile() -> dict | None:
    """Profile summary of the signed-in user (None when anonymous or unprofiled)."""
    if not current_user.is_authenticated:
        return None
    return session_profile(current_user)


def needs_role_confirmation(user: User) -> bool:
    return user.role != GESTOR_ROLE and not user.role_confirmed


def pending_onboarding_step(user: User) -> str | None:
    """Data notice, then the introduction once (neither for the gestor)."""
    if needs_data_notice(user):
        return DATA_NOTICE_ENDPOINT
    if user.intro_vista_at is None and user.role != GESTOR_ROLE:
        return INTRODUCTION_ENDPOINT
    return None


def pending_profile_step(user: User) -> str | None:
    """Endpoint the user must visit before using the tools, if any.

    The gestor uses the original app ("modo original"): no questionnaire.
    """
    step = pending_onboarding_step(user)
    if step is not None:
        return step
    if user.role == GESTOR_ROLE:
        return None
    if session_profile(user) is None:
        return "profile.questionnaire"
    if needs_role_confirmation(user):
        return "profile.result"
    return None


def require_current_profile(endpoint: str):
    """Access check: no page without the notice, no tool without a profile and role."""
    if endpoint == DATA_NOTICE_ENDPOINT:
        return None
    step = pending_profile_step(current_user)
    if step is None or (endpoint == INTRODUCTION_ENDPOINT and step != DATA_NOTICE_ENDPOINT):
        return None
    if step == INTRODUCTION_ENDPOINT:
        return redirect(url_for(step))
    if step != DATA_NOTICE_ENDPOINT and (endpoint.startswith(PROFILE_ENDPOINT_PREFIX) or endpoint == GUIDE_ENDPOINT):
        return None
    message = {
        DATA_NOTICE_ENDPOINT: DATA_NOTICE_MESSAGE,
        "profile.questionnaire": QUESTIONNAIRE_MESSAGE,
    }.get(step, ROLE_MESSAGE)
    if wants_json():
        return jsonify({"error": message}), 403
    flash(message, "info")
    return redirect(url_for(step))


def after_login(user: User):
    """After-sign-in hook: profile into the session; questionnaire if missing."""
    refresh_profile_session(user)
    step = pending_profile_step(user)
    return redirect(url_for(step)) if step else None


def init_app(app: Flask) -> None:
    app.extensions[_EXTENSION_KEY] = app.config.get("QUESTIONNAIRE_PATH")
    # Fail at start-up (with the validation message) if the YAML is malformed.
    load_questionnaire(app.extensions[_EXTENSION_KEY])
    app.extensions[_NOTICES_KEY] = app.config.get("AVISOS_PATH")
    load_notices(app.extensions[_NOTICES_KEY])
    guard.register_access_check(app, require_current_profile)
    register_after_login(app, after_login)

    @app.context_processor
    def inject_current_profile():
        return {"current_profile": current_profile()}
