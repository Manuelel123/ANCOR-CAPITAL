"""Load and validate the risk-profiling questionnaire (``config/cuestionario.yaml``).

Questions, options, thresholds, vetoes, horizon caps, the limiting-factor
tie-break and the suggested-role rule all live in the YAML file; this module
turns it into immutable objects and raises ``QuestionnaireError`` with a clear
message when the file is malformed. No Flask imports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import yaml

# <repo>/config/cuestionario.yaml (this file is <repo>/src/optimizacion_portafolios/profiling/).
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "cuestionario.yaml"
PATH_ENV_VAR = "ATLAS_QUESTIONNAIRE_PATH"
MIN_OPTIONS = 2
# Roles the questionnaire may suggest ("gestor" is never suggested nor chosen).
SUGGESTABLE_ROLES = ("novato", "inversionista")
VETO_FACTOR = "veto"
HORIZON_FACTOR = "horizonte"
SCORE_FACTOR = "tolerancia"
INVESTS_ROUTE = "ya_invierte"
STARTS_ROUTE = "empieza_de_cero"
CATEGORY_ROUTES = (STARTS_ROUTE, INVESTS_ROUTE)
CAPITAL_FIELD = "capital_rango"
CAPITAL_AMOUNT_FIELD = "capital_monto"
HORIZON_FIELD = "horizonte"
PRIOR_FIELDS = (CAPITAL_FIELD, HORIZON_FIELD)


class QuestionnaireError(ValueError):
    """The questionnaire file is missing or malformed."""


@dataclass(frozen=True)
class Level:
    id: str
    label: str
    description: str


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    description: str


@dataclass(frozen=True)
class Choice:
    """An option of the prior data (capital range or horizon)."""

    id: str
    text: str
    max_level: str | None = None  # horizon cap; None = no cap
    capital_level: str | None = None  # bajo, medio o alto (capital only)
    low: int = 0  # capital only, in pesos
    high: int | None = None


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    options: tuple[str, ...]  # answers are 1-based positions
    correct: int | None = None


@dataclass(frozen=True)
class Block:
    id: str
    title: str
    description: str
    questions: tuple[Question, ...]
    # Fields of the step in display order: question ids, capital and horizon.
    layout: tuple[str, ...]


@dataclass(frozen=True)
class Veto:
    question: str
    option: int  # 1-based position
    option_text: str
    explanation: str


@dataclass(frozen=True)
class RoleRule:
    experience_question: str
    min_experience: int  # 1-based position; this option or a later one
    control_question: str | None  # optional knowledge check
    if_met: str
    otherwise: str


@dataclass(frozen=True)
class Questionnaire:
    version: str
    levels: tuple[Level, ...]
    thresholds: tuple[tuple[str, Fraction], ...]  # (level id, minimum score), ascending
    score_fields: tuple[str, ...]  # what adds up to the score (horizon or question ids)
    capacity_question: str
    capital_text: str
    capital_options: tuple[Choice, ...]
    horizon_text: str
    horizon_options: tuple[Choice, ...]
    blocks: tuple[Block, ...]
    vetoes: tuple[Veto, ...]
    tiebreak: tuple[str, ...]
    factor_labels: dict
    explanations: dict
    role_rule: RoleRule
    capital_levels: tuple[str, ...] = ()
    categories: tuple[Category, ...] = ()
    # "ya_invierte" or "empieza_de_cero" -> risk level -> category id.
    category_matrix: dict = field(default_factory=dict)
    legacy_fund_complete_from: int = 1
    broker_question: str = ""
    ibkr_option: int = 0  # 1-based position
    # Choices of earlier versions, for the saved profiles that still use them.
    legacy_capital: tuple[Choice, ...] = ()
    legacy_horizon: tuple[Choice, ...] = ()
    source: str = ""

    # Lookups -----------------------------------------------------------------
    @property
    def level_ids(self) -> tuple[str, ...]:
        return tuple(level.id for level in self.levels)

    def level(self, level_id: str) -> Level:
        for level in self.levels:
            if level.id == level_id:
                return level
        raise KeyError(level_id)

    def level_rank(self, level_id: str) -> int:
        return self.level_ids.index(level_id)

    def question(self, question_id: str) -> Question:
        for block in self.blocks:
            for question in block.questions:
                if question.id == question_id:
                    return question
        raise KeyError(question_id)

    def block(self, block_id: str) -> Block:
        for block in self.blocks:
            if block.id == block_id:
                return block
        raise KeyError(block_id)

    @property
    def max_score(self) -> int:
        return sum(
            len(self.horizon_options if field == HORIZON_FACTOR else self.question(field).options) - 1
            for field in self.score_fields
        )

    @property
    def question_ids(self) -> tuple[str, ...]:
        return tuple(question.id for block in self.blocks for question in block.questions)

    def capital(self, choice_id: str) -> Choice:
        return _find_choice(self.capital_options, choice_id)

    def horizon(self, choice_id: str) -> Choice:
        return _find_choice(self.horizon_options, choice_id)

    def capital_text_of(self, choice_id: str) -> str:
        """Label of a capital range, also from earlier versions of the questionnaire."""
        return _find_choice((*self.capital_options, *self.legacy_capital), choice_id).text

    def horizon_text_of(self, choice_id: str) -> str:
        return _find_choice((*self.horizon_options, *self.legacy_horizon), choice_id).text

    def capital_range_for(self, amount: int) -> str:
        """Id of the range that contains ``amount`` (pesos)."""
        return [choice.id for choice in self.capital_options if choice.low <= amount][-1]

    def capital_bounds(self, choice_id: str) -> tuple[int, int | None]:
        """``(low, high)`` in pesos of a range, also of earlier versions (``None``: no top)."""
        for position, choice in enumerate(self.capital_options):
            if choice.id == choice_id:
                following = self.capital_options[position + 1:]
                return choice.low, following[0].low if following else None
        choice = _find_choice(self.legacy_capital, choice_id)
        return choice.low, choice.high

    def capital_level(self, choice_id: str) -> str:
        """``bajo``, ``medio`` or ``alto``: which instruments to show, never a limit."""
        return _find_choice((*self.capital_options, *self.legacy_capital), choice_id).capital_level

    def category(self, category_id: str) -> Category:
        for category in self.categories:
            if category.id == category_id:
                return category
        raise KeyError(category_id)

    @property
    def steps(self) -> tuple[str, ...]:
        """Wizard steps: one per block."""
        return tuple(block.id for block in self.blocks)


def _find_choice(choices: tuple[Choice, ...], choice_id: str) -> Choice:
    for choice in choices:
        if choice.id == choice_id:
            return choice
    raise KeyError(choice_id)


# Parsing -----------------------------------------------------------------------
def _fail(source: str, message: str):
    raise QuestionnaireError(f"{source}: {message}")


def _text(source: str, value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(source, f"falta el texto de {where} (debe ser una cadena no vacía).")
    return " ".join(value.split())


def _mapping(source: str, value, where: str) -> dict:
    if not isinstance(value, dict):
        _fail(source, f"«{where}» debe ser un mapa (clave: valor).")
    return value


def _sequence(source: str, value, where: str) -> list:
    if not isinstance(value, list) or not value:
        _fail(source, f"«{where}» debe ser una lista no vacía.")
    return value


def _identifier(source: str, value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(source, f"{where} necesita un «id» de texto (use comillas si parece un número).")
    return value.strip()


def _unique(source: str, ids: list[str], where: str) -> None:
    seen = set()
    for item in ids:
        if item in seen:
            _fail(source, f"id repetido «{item}» en {where}.")
        seen.add(item)


def parse_questionnaire(data, source: str = "cuestionario") -> Questionnaire:
    """Validate the YAML structure ``data`` and build a ``Questionnaire``."""
    data = _mapping(source, data, "raíz del archivo")
    version = data.get("version")
    if version is None or isinstance(version, bool) or str(version).strip() == "":
        _fail(source, "falta «version».")
    version = str(version).strip()

    levels = []
    for index, raw in enumerate(_sequence(source, data.get("niveles"), "niveles"), start=1):
        raw = _mapping(source, raw, f"niveles[{index}]")
        level_id = _identifier(source, raw.get("id"), f"niveles[{index}]")
        levels.append(Level(level_id, _text(source, raw.get("etiqueta"), f"nivel {level_id}"),
                            _text(source, raw.get("descripcion"), f"la descripción del nivel {level_id}")))
    if len(levels) < 2:
        _fail(source, "«niveles» necesita al menos dos niveles.")
    level_ids = [level.id for level in levels]
    _unique(source, level_ids, "niveles")

    raw_thresholds = _mapping(source, data.get("umbrales"), "umbrales")
    if set(raw_thresholds) != set(level_ids[1:]):
        _fail(source, f"«umbrales» debe tener exactamente los niveles {', '.join(level_ids[1:])} "
                      f"(el primero, {level_ids[0]}, no lleva umbral).")
    thresholds = []
    previous = Fraction(0)
    for level_id in level_ids[1:]:
        value = raw_thresholds[level_id]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail(source, f"el umbral de {level_id} debe ser un número.")
        value = Fraction(str(value))
        if not previous < value:
            _fail(source, f"los umbrales deben crecer (revise {level_id}).")
        thresholds.append((level_id, value))
        previous = value

    prior = _mapping(source, data.get("datos_previos"), "datos_previos")
    capital = _mapping(source, prior.get("capital"), "datos_previos.capital")
    horizon = _mapping(source, prior.get("horizonte"), "datos_previos.horizonte")
    capital_levels = tuple(_sequence(source, data.get("niveles_capital"), "niveles_capital"))

    def capital_choice(choice_id, raw, where) -> Choice:
        level = raw.get("nivel")
        if level not in capital_levels:
            _fail(source, f"{where}: «{choice_id}» necesita un «nivel» de {', '.join(capital_levels)}.")
        low = raw.get("desde")
        if isinstance(low, bool) or not isinstance(low, int) or low < 0:
            _fail(source, f"{where}: «{choice_id}» necesita «desde», un monto en pesos.")
        return Choice(choice_id, _text(source, raw.get("texto"), where), capital_level=level, low=low,
                      high=raw.get("hasta"))

    capital_options = tuple(
        capital_choice(_identifier(source, item.get("id"), "una opción de capital"), item, "datos_previos.capital")
        for item in (_mapping(source, raw, "datos_previos.capital.opciones")
                     for raw in _sequence(source, capital.get("opciones"), "datos_previos.capital.opciones"))
    )
    _unique(source, [choice.id for choice in capital_options], "las opciones de capital")
    if not capital_options or capital_options[0].low != 0:
        _fail(source, "la primera opción de capital debe tener «desde: 0».")
    lows = [choice.low for choice in capital_options]
    if lows != sorted(set(lows)):
        _fail(source, "los montos «desde» del capital deben crecer.")
    horizon_options = []
    for raw in _sequence(source, horizon.get("opciones"), "datos_previos.horizonte.opciones"):
        raw = _mapping(source, raw, "datos_previos.horizonte.opciones")
        choice_id = _identifier(source, raw.get("id"), "una opción de horizonte")
        if "nivel_maximo" not in raw:
            _fail(source, f"la opción de horizonte «{choice_id}» necesita «nivel_maximo» (un nivel o null).")
        cap = raw.get("nivel_maximo")
        if cap is not None and cap not in level_ids:
            _fail(source, f"nivel_maximo «{cap}» del horizonte «{choice_id}» no es un nivel.")
        horizon_options.append(Choice(choice_id, _text(source, raw.get("texto"), f"el horizonte {choice_id}"), cap))
    _unique(source, [choice.id for choice in horizon_options], "las opciones de horizonte")

    blocks = []
    for raw in _sequence(source, data.get("bloques"), "bloques"):
        raw = _mapping(source, raw, "bloques")
        block_id = _identifier(source, raw.get("id"), "un bloque")
        if block_id in (VETO_FACTOR, HORIZON_FACTOR):
            _fail(source, f"«{block_id}» es un nombre reservado; use otro id de bloque.")
        questions = []
        layout = []
        for raw_question in _sequence(source, raw.get("preguntas"), f"bloque {block_id}.preguntas"):
            raw_question = _mapping(source, raw_question, f"bloque {block_id}.preguntas")
            if "campo" in raw_question:
                # Capital and horizon keep their own columns; here only their place.
                if raw_question["campo"] not in PRIOR_FIELDS:
                    _fail(source, f"«campo» debe ser uno de: {', '.join(PRIOR_FIELDS)}.")
                layout.append(raw_question["campo"])
                continue
            question_id = _identifier(source, raw_question.get("id"), f"una pregunta del bloque {block_id}")
            options = _sequence(source, raw_question.get("opciones"), f"opciones de {question_id}")
            if len(options) < MIN_OPTIONS:
                _fail(source, f"la pregunta «{question_id}» debe tener al menos {MIN_OPTIONS} opciones (tiene {len(options)}).")
            options = tuple(_text(source, option, f"una opción de {question_id}") for option in options)
            _unique(source, list(options), f"las opciones de {question_id}")
            correct = raw_question.get("correcta")
            if correct is not None and (
                isinstance(correct, bool) or not isinstance(correct, int) or not 1 <= correct <= len(options)
            ):
                _fail(source, f"«correcta» de «{question_id}» debe ser un número de 1 a {len(options)}.")
            questions.append(Question(question_id, _text(source, raw_question.get("texto"), question_id), options, correct))
            layout.append(question_id)
        blocks.append(Block(block_id, _text(source, raw.get("titulo"), f"el bloque {block_id}"),
                            str(raw.get("descripcion") or "").strip(), tuple(questions), tuple(layout)))
    _unique(source, [block.id for block in blocks], "bloques")
    question_ids = [question.id for block in blocks for question in block.questions]
    _unique(source, question_ids, "las preguntas")
    placed = [field for block in blocks for field in block.layout if field in PRIOR_FIELDS]
    if sorted(placed) != sorted(PRIOR_FIELDS):
        _fail(source, f"los bloques deben ubicar una vez cada uno de: {', '.join(PRIOR_FIELDS)}.")
    questions_by_id = {question.id: question for block in blocks for question in block.questions}

    scoring = _mapping(source, data.get("puntaje"), "puntaje")
    score_fields = tuple(_sequence(source, scoring.get("preguntas"), "puntaje.preguntas"))
    max_score = 0
    for field in score_fields:
        if field == HORIZON_FACTOR:
            max_score += len(horizon_options) - 1
        elif field in questions_by_id:
            max_score += len(questions_by_id[field].options) - 1
        else:
            _fail(source, f"puntaje.preguntas: «{field}» no es el horizonte ni una pregunta.")
    if thresholds and thresholds[-1][1] > max_score:
        _fail(source, f"los umbrales no pueden pasar del puntaje máximo ({max_score}).")
    capacity_question = scoring.get("capacidad")
    if capacity_question not in questions_by_id:
        _fail(source, f"puntaje.capacidad: la pregunta «{capacity_question}» no existe.")

    def option_position(question_id, option_text, where) -> int:
        if question_id not in questions_by_id:
            _fail(source, f"{where}: la pregunta «{question_id}» no existe.")
        options = questions_by_id[question_id].options
        text = " ".join(str(option_text or "").split())
        if text not in options:
            _fail(source, f"{where}: «{option_text}» no es una opción de «{question_id}».")
        return options.index(text) + 1

    vetoes = []
    for raw in data.get("vetos") or []:
        raw = _mapping(source, raw, "vetos")
        question_id = raw.get("pregunta")
        position = option_position(question_id, raw.get("opcion"), "vetos")
        vetoes.append(Veto(question_id, position, questions_by_id[question_id].options[position - 1],
                           _text(source, raw.get("explicacion"), f"el veto de {question_id}")))

    factors = _mapping(source, data.get("factor_limitante"), "factor_limitante")
    tiebreak = tuple(_sequence(source, factors.get("desempate"), "factor_limitante.desempate"))
    expected = {VETO_FACTOR, HORIZON_FACTOR, SCORE_FACTOR}
    if set(tiebreak) != expected or len(tiebreak) != len(expected):
        _fail(source, "factor_limitante.desempate debe listar una vez cada uno de: "
                      + ", ".join(sorted(expected)) + ".")
    labels = _mapping(source, factors.get("etiquetas"), "factor_limitante.etiquetas")
    explanations = _mapping(source, factors.get("explicaciones"), "factor_limitante.explicaciones")
    factor_labels = {factor: _text(source, label, f"la etiqueta del factor {factor}") for factor, label in labels.items()}
    for factor in tiebreak:
        _text(source, labels.get(factor), f"la etiqueta del factor {factor}")
    templates = {}
    sample = {"perfil": "x", "horizonte": "x", "puntaje": "x", "puntaje_maximo": "x"}
    for factor in tiebreak:
        if factor == VETO_FACTOR:
            continue  # each veto carries its own explanation
        template = _text(source, explanations.get(factor), f"la explicación del factor {factor}")
        try:
            template.format_map(sample)
        except (KeyError, ValueError, IndexError) as error:
            _fail(source, f"la explicación del factor {factor} usa un marcador inválido: {error}.")
        templates[factor] = template

    rule = _mapping(source, data.get("rol_sugerido"), "rol_sugerido")
    experience = rule.get("experiencia")
    min_experience = option_position(experience, rule.get("experiencia_minima"), "rol_sugerido.experiencia_minima")
    control = rule.get("pregunta_control")
    if control is not None and (control not in questions_by_id or questions_by_id[control].correct is None):
        _fail(source, f"rol_sugerido.pregunta_control «{control}» debe ser una pregunta con «correcta».")
    roles = (rule.get("si_cumple"), rule.get("si_no"))
    for role in roles:
        if role not in SUGGESTABLE_ROLES:
            _fail(source, f"rol_sugerido: «{role}» no es un rol sugerible ({', '.join(SUGGESTABLE_ROLES)}).")

    raw_categories = _mapping(source, data.get("categorias"), "categorias")
    categories = []
    for raw in _sequence(source, raw_categories.get("niveles"), "categorias.niveles"):
        raw = _mapping(source, raw, "categorias.niveles")
        category_id = _identifier(source, raw.get("id"), "una categoría")
        categories.append(Category(category_id, _text(source, raw.get("etiqueta"), f"categoría {category_id}"),
                                   _text(source, raw.get("descripcion"), f"la descripción de {category_id}")))
    category_ids = [category.id for category in categories]
    matrix = _mapping(source, raw_categories.get("matriz"), "categorias.matriz")
    for route in CATEGORY_ROUTES:
        row = _mapping(source, matrix.get(route), f"categorias.matriz.{route}")
        for level_id in level_ids:
            if row.get(level_id) not in category_ids:
                _fail(source, f"categorias.matriz.{route}.{level_id} debe ser una de: {', '.join(category_ids)}.")

    broker = _mapping(source, data.get("cuenta_broker"), "cuenta_broker")
    ibkr_option = option_position(broker.get("pregunta"), broker.get("opcion_ibkr"), "cuenta_broker")

    legacy = data.get("legado") or {}
    legacy_capital = tuple(
        capital_choice(choice_id, _mapping(source, raw, "legado.capital"), "legado.capital")
        for choice_id, raw in (legacy.get("capital") or {}).items()
    )
    legacy_horizon = tuple(
        Choice(choice_id, _text(source, text, "legado.horizonte"))
        for choice_id, text in (legacy.get("horizonte") or {}).items()
    )

    return Questionnaire(
        version=version,
        levels=tuple(levels),
        thresholds=tuple(thresholds),
        score_fields=score_fields,
        capacity_question=capacity_question,
        capital_text=_text(source, capital.get("texto"), "la pregunta de capital"),
        capital_options=capital_options,
        horizon_text=_text(source, horizon.get("texto"), "la pregunta de horizonte"),
        horizon_options=tuple(horizon_options),
        blocks=tuple(blocks),
        vetoes=tuple(vetoes),
        tiebreak=tiebreak,
        factor_labels=factor_labels,
        explanations=templates,
        role_rule=RoleRule(experience, min_experience, control, roles[0], roles[1]),
        capital_levels=capital_levels,
        categories=tuple(categories),
        category_matrix=matrix,
        legacy_fund_complete_from=int(legacy.get("fondo_completo_desde") or 1),
        broker_question=broker["pregunta"],
        ibkr_option=ibkr_option,
        legacy_capital=legacy_capital,
        legacy_horizon=legacy_horizon,
        source=source,
    )


def questionnaire_path(path: str | os.PathLike | None = None) -> Path:
    return Path(path or os.environ.get(PATH_ENV_VAR) or DEFAULT_PATH)


@lru_cache(maxsize=8)
def _load_cached(path: str, _mtime_ns: int) -> Questionnaire:
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as error:
        raise QuestionnaireError(f"No se encontró el cuestionario en {path}.") from error
    except yaml.YAMLError as error:
        raise QuestionnaireError(f"{path}: el YAML no es válido: {error}") from error
    return parse_questionnaire(data, source=path)


def load_questionnaire(path: str | os.PathLike | None = None) -> Questionnaire:
    """Load (and cache until the file changes) the questionnaire at ``path``.

    Defaults to ``ATLAS_QUESTIONNAIRE_PATH`` or ``config/cuestionario.yaml``.
    """
    resolved = questionnaire_path(path)
    try:
        mtime = resolved.stat().st_mtime_ns
    except FileNotFoundError as error:
        raise QuestionnaireError(f"No se encontró el cuestionario en {resolved}.") from error
    return _load_cached(str(resolved), mtime)
