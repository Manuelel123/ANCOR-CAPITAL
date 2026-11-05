"""Risk profile from the questionnaire answers (pure functions, no Flask).

Rules (data in ``config/cuestionario.yaml``):

1. The score adds the fields of ``puntaje.preguntas``; each one is worth 0 to 3
   by its position (the most cautious option is worth 0).
2. The score level is the highest level whose threshold the score reaches.
3. Two rules override the score: the horizon caps the profile
   (``nivel_maximo``; ``null`` = no cap) and a veto answer forces the lowest
   level. Nothing is blocked: only the profile changes.
4. Capital and knowledge never change the risk profile. Capital only decides
   which instruments are shown.
5. Limiting factor: a veto if any applies; otherwise the first factor of
   ``factor_limitante.desempate`` whose level equals the final one (the
   horizon only counts when it imposes a cap).
6. Role: ``si_cumple`` when the experience answer is at least
   ``experiencia_minima`` (and the control question, if any, is answered
   correctly), else ``si_no``. It is assigned when the profile is saved.
7. Category: ``categorias.matriz`` by final profile and whether the person
   already invests. Capital never changes it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction

from optimizacion_portafolios.profiling.questionnaire import (
    HORIZON_FACTOR,
    INVESTS_ROUTE,
    SCORE_FACTOR,
    STARTS_ROUTE,
    VETO_FACTOR,
    Category,
    Questionnaire,
    Veto,
)

CAPACITY_KEY = "capacidad"


class AnswerError(ValueError):
    """Missing or invalid answers."""


@dataclass(frozen=True)
class ProfileResult:
    version: str
    capital: str
    horizon: str
    answers: dict[str, int]
    scores: dict[str, float]  # score and the capacity answer
    block_levels: dict[str, str]
    horizon_level: str  # the cap, or the highest level when there is none
    horizon_capped: bool
    final_level: str
    limiting_factor: str
    veto: Veto | None
    suggested_role: str
    vetoes: tuple[Veto, ...] = field(default=())


def _coerce_answer(value, options: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= options and str(value).strip() == str(number) else None


def clean_answers(questionnaire: Questionnaire, raw: Mapping) -> dict[str, int]:
    """Valid answers in ``raw`` (unknown questions and bad values are dropped)."""
    answers = {}
    for question_id in questionnaire.question_ids:
        if question_id in raw:
            value = _coerce_answer(raw[question_id], len(questionnaire.question(question_id).options))
            if value is not None:
                answers[question_id] = value
    return answers


def validate(questionnaire: Questionnaire, capital: str, horizon: str, answers: Mapping) -> dict[str, int]:
    """Clean answers of a complete questionnaire; ``AnswerError`` if anything is missing."""
    try:
        questionnaire.capital(capital)
    except KeyError as error:
        raise AnswerError("Elige un rango de capital.") from error
    try:
        questionnaire.horizon(horizon)
    except KeyError as error:
        raise AnswerError("Elige un horizonte de inversión.") from error
    cleaned = clean_answers(questionnaire, answers)
    missing = [question_id for question_id in questionnaire.question_ids if question_id not in cleaned]
    if missing:
        raise AnswerError("Faltan respuestas: " + ", ".join(missing) + ".")
    return cleaned


def risk_score(questionnaire: Questionnaire, horizon: str, answers: Mapping[str, int]) -> int:
    """Sum of the scored fields; each one is worth its 0-based position."""
    horizon_ids = [choice.id for choice in questionnaire.horizon_options]
    return sum(
        horizon_ids.index(horizon) if name == HORIZON_FACTOR else answers[name] - 1
        for name in questionnaire.score_fields
    )


def level_for_score(questionnaire: Questionnaire, score) -> str:
    score = Fraction(str(score)) if isinstance(score, float) else Fraction(score)
    level = questionnaire.levels[0].id
    for level_id, minimum in questionnaire.thresholds:
        if score >= minimum:
            level = level_id
    return level


def triggered_vetoes(questionnaire: Questionnaire, answers: Mapping[str, int]) -> tuple[Veto, ...]:
    return tuple(veto for veto in questionnaire.vetoes if answers.get(veto.question) == veto.option)


def suggest_role(questionnaire: Questionnaire, answers: Mapping[str, int]) -> str:
    rule = questionnaire.role_rule
    experienced = already_invests(questionnaire, answers)
    knows = True
    if rule.control_question is not None:
        knows = answers.get(rule.control_question) == questionnaire.question(rule.control_question).correct
    return rule.if_met if experienced and knows else rule.otherwise


def already_invests(questionnaire: Questionnaire, answers: Mapping[str, int]) -> bool:
    """True when the experience answer says the person already invests."""
    rule = questionnaire.role_rule
    return answers.get(rule.experience_question, 0) >= rule.min_experience


def has_ibkr_account(questionnaire: Questionnaire, answers: Mapping[str, int]) -> bool:
    """True when the person already has an Interactive Brokers account."""
    return answers.get(questionnaire.broker_question) == questionnaire.ibkr_option


def category_for(questionnaire: Questionnaire, final_level: str, answers: Mapping[str, int]) -> Category:
    """Diagnosis category from the final profile and whether the person invests."""
    route = INVESTS_ROUTE if already_invests(questionnaire, answers) else STARTS_ROUTE
    return questionnaire.category(questionnaire.category_matrix[route][final_level])


def emergency_fund_complete(questionnaire: Questionnaire, version: str, answers: Mapping[str, int]) -> bool:
    """Whether the fund is complete; version 1 measured it in months with other options."""
    if version == questionnaire.version:
        return not triggered_vetoes(questionnaire, answers)
    return answers.get(questionnaire.capacity_question, 0) >= questionnaire.legacy_fund_complete_from


def evaluate(questionnaire: Questionnaire, capital: str, horizon: str, answers: Mapping) -> ProfileResult:
    """Profile of a complete set of answers (raises ``AnswerError`` if incomplete)."""
    answers = validate(questionnaire, capital, horizon, answers)
    rank = questionnaire.level_rank
    score = risk_score(questionnaire, horizon, answers)
    score_level = level_for_score(questionnaire, score)
    cap = questionnaire.horizon(horizon).max_level
    horizon_level = cap or questionnaire.levels[-1].id
    candidates = {SCORE_FACTOR: score_level, HORIZON_FACTOR: horizon_level}
    vetoes = triggered_vetoes(questionnaire, answers)
    if vetoes:
        final = questionnaire.levels[0].id
        factor = VETO_FACTOR
    else:
        final = min(candidates.values(), key=rank)
        factor = next(
            name
            for name in questionnaire.tiebreak
            if name != VETO_FACTOR
            and candidates[name] == final
            and (name != HORIZON_FACTOR or cap is not None)
        )
    return ProfileResult(
        version=questionnaire.version,
        capital=capital,
        horizon=horizon,
        answers=answers,
        scores={SCORE_FACTOR: float(score), CAPACITY_KEY: float(answers[questionnaire.capacity_question])},
        block_levels={
            SCORE_FACTOR: score_level,
            CAPACITY_KEY: questionnaire.levels[0].id if vetoes else questionnaire.levels[-1].id,
        },
        horizon_level=horizon_level,
        horizon_capped=cap is not None,
        final_level=final,
        limiting_factor=factor,
        veto=vetoes[0] if vetoes else None,
        suggested_role=suggest_role(questionnaire, answers),
        vetoes=vetoes,
    )


def format_score(value: float) -> str:
    """``2.4`` -> ``"2,40"`` (averages of questionnaire version 1)."""
    return f"{value:.2f}".replace(".", ",")


def format_points(value: float) -> str:
    """``6.0`` -> ``"6"`` (the score is a whole number of points)."""
    return f"{value:g}"


def explain(questionnaire: Questionnaire, result: ProfileResult) -> str:
    """Plain-language reason for the final profile (shown to the user)."""
    if result.limiting_factor == VETO_FACTOR and result.veto is not None:
        return result.veto.explanation
    horizon_text = questionnaire.horizon(result.horizon).text
    return questionnaire.explanations[result.limiting_factor].format_map(
        {
            "perfil": questionnaire.level(result.final_level).label,
            "horizonte": horizon_text[:1].lower() + horizon_text[1:],
            "puntaje": format_points(result.scores[SCORE_FACTOR]),
            "puntaje_maximo": questionnaire.max_score,
        }
    )
