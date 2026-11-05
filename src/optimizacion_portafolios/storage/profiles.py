"""Risk profiles (``perfiles``) and unfinished questionnaires (``perfil_borradores``).

Profiles are never overwritten: ``save_profile`` inserts a new row and, in
the same transaction, clears ``vigente`` on the user's previous rows, so each
user has at most one current profile (also enforced by a partial unique index).
"""

from __future__ import annotations

from sqlalchemy import select, update

from optimizacion_portafolios.profiling import ProfileResult
from optimizacion_portafolios.profiling.questionnaire import CAPITAL_AMOUNT_FIELD
from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import Profile, ProfileDraft

TOLERANCE_BLOCK = "tolerancia"
CAPACITY_BLOCK = "capacidad"


def get_current_profile(user_id: int | str) -> Profile | None:
    return db.session.scalar(
        select(Profile).where(Profile.usuario_id == int(user_id), Profile.vigente.is_(True))
    )


def is_current_profile(user_id: int | str, profile_id) -> bool:
    """Whether ``profile_id`` is still the user's current (vigente) profile."""
    if profile_id is None:
        return False
    return db.session.scalar(
        select(Profile.id).where(
            Profile.id == int(profile_id),
            Profile.usuario_id == int(user_id),
            Profile.vigente.is_(True),
        )
    ) is not None


def list_profiles(user_id: int | str) -> list[Profile]:
    """Every profile of the user, newest first."""
    return list(
        db.session.scalars(
            select(Profile)
            .where(Profile.usuario_id == int(user_id))
            .order_by(Profile.fecha.desc(), Profile.id.desc())
        )
    )


def save_profile(
    user_id: int | str, result: ProfileResult, *, delete_draft: bool = True, capital_amount: int | None = None
) -> Profile:
    """Insert ``result`` as the user's current profile (one transaction, then commit)."""
    user_id = int(user_id)
    try:
        db.session.execute(
            update(Profile)
            .where(Profile.usuario_id == user_id, Profile.vigente.is_(True))
            .values(vigente=False)
        )
        profile = Profile(
            usuario_id=user_id,
            version_cuestionario=result.version,
            capital_rango=result.capital,
            horizonte=result.horizon,
            respuestas={**result.answers, **({CAPITAL_AMOUNT_FIELD: capital_amount} if capital_amount else {})},
            puntaje_tolerancia=result.scores[TOLERANCE_BLOCK],
            puntaje_capacidad=result.scores[CAPACITY_BLOCK],
            nivel_tolerancia=result.block_levels[TOLERANCE_BLOCK],
            nivel_capacidad=result.block_levels[CAPACITY_BLOCK],
            nivel_horizonte=result.horizon_level,
            perfil_final=result.final_level,
            factor_limitante=result.limiting_factor,
            rol_sugerido=result.suggested_role,
            vigente=True,
        )
        db.session.add(profile)
        if delete_draft:
            draft = get_draft(user_id)
            if draft is not None:
                db.session.delete(draft)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return profile


def get_draft(user_id: int | str) -> ProfileDraft | None:
    return db.session.scalar(select(ProfileDraft).where(ProfileDraft.usuario_id == int(user_id)))


def save_draft(user_id: int | str, version: str, step: int, data: dict) -> ProfileDraft:
    """Create or replace the user's unfinished questionnaire (and commit)."""
    draft = get_draft(user_id)
    if draft is None:
        draft = ProfileDraft(usuario_id=int(user_id))
        db.session.add(draft)
    draft.version_cuestionario = version
    draft.paso = step
    draft.datos = dict(data)  # new object so the JSON column is flagged as changed
    db.session.commit()
    return draft


def delete_draft(user_id: int | str) -> None:
    draft = get_draft(user_id)
    if draft is not None:
        db.session.delete(draft)
        db.session.commit()
