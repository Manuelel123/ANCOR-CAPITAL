"""Load and validate the guide (``config/guia_ibkr.yaml``); no Flask here."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[4] / "config" / "guia_ibkr.yaml"
PATH_ENV_VAR = "ATLAS_GUIA_IBKR_PATH"
INSTRUMENTS_PATH = DEFAULT_PATH.with_name("instrumentos.yaml")
INSTRUMENTS_ENV_VAR = "ATLAS_INSTRUMENTOS_PATH"
OFFICIAL_LINK = "https://www.interactivebrokers.com"

_TEXT_FIELDS = (
    "verificado",
    "referencia",
    "criterio.titulo",
    "estado_referido.sin_acuerdo",
    "estado_referido.con_acuerdo",
    "aviso_comision",
    "alternativa",
    "capital.bajo.nota",
    "capital.bajo.ejemplos_titulo",
    "capital.bajo.ejemplos_nota",
    "capital.bajo.ejemplos_fuente",
    "capital.bajo.ejemplos_verificado",
    "capital.medio_alto.nota",
    "datos_apoyo.titulo",
    "datos_apoyo.contras_titulo",
    "por_confirmar.titulo",
)
_LIST_FIELDS = (
    "criterio.puntos",
    "capital.bajo.ejemplos",
    "pasos",
    "datos_apoyo.puntos",
    "datos_apoyo.contras",
    "por_confirmar.puntos",
    "fuentes",
)
_ITEM_FIELDS = {
    "capital.bajo.ejemplos": ("nombre", "desde"),
    "pasos": ("titulo", "que_hacer", "por_que"),
    "fuentes": ("titulo", "url", "fecha"),
}


class GuideError(ValueError):
    """The guide file is missing or invalid."""


def _value(data: dict, dotted: str):
    value = data
    for key in dotted.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def parse_guide(data, source: str = "guia") -> dict:
    """Check the YAML content and return it; ``GuideError`` names what is wrong."""
    if not isinstance(data, dict):
        raise GuideError(f"{source}: el archivo debe ser un mapa YAML.")
    if not isinstance(data.get("referido_ibkr"), bool):
        raise GuideError(f"{source}: «referido_ibkr» debe ser true o false.")
    if data.get("enlace_oficial") != OFFICIAL_LINK:
        raise GuideError(f"{source}: «enlace_oficial» debe ser {OFFICIAL_LINK}, sin código de referido.")
    for field in _TEXT_FIELDS:
        value = _value(data, field)
        if not isinstance(value, str) or not value.strip():
            raise GuideError(f"{source}: falta el texto de «{field}».")
    for field in _LIST_FIELDS:
        items = _value(data, field)
        if not isinstance(items, list) or not items:
            raise GuideError(f"{source}: «{field}» debe ser una lista con contenido.")
        for position, item in enumerate(items, start=1):
            for key in _ITEM_FIELDS.get(field, ()):
                if not isinstance(item, dict) or not str(item.get(key) or "").strip():
                    raise GuideError(f"{source}: falta «{key}» en «{field}» número {position}.")
    return data


def parse_instruments(data, source: str = "instrumentos") -> dict:
    """Check the catalog and return it; ``GuideError`` names what is wrong."""
    if not isinstance(data, dict):
        raise GuideError(f"{source}: el archivo debe ser un mapa YAML.")
    for field in ("verificado", "nota", "fondo_incompleto", "hrp_destacado_desde"):
        if not str(data.get(field) or "").strip():
            raise GuideError(f"{source}: falta el texto de «{field}».")
    steps = data.get("pasos")
    if not isinstance(steps, dict) or not {"hrp_guiado", "hrp_libre"} <= set(steps):
        raise GuideError(f"{source}: «pasos» debe incluir hrp_guiado y hrp_libre.")
    for step_id, step in steps.items():
        for key in ("titulo", "descripcion", "destino"):
            if not isinstance(step, dict) or not str(step.get(key) or "").strip():
                raise GuideError(f"{source}: falta «{key}» en el paso «{step_id}».")
    journey = data.get("recorrido")
    if not isinstance(journey, dict) or not journey:
        raise GuideError(f"{source}: falta «recorrido».")
    for category, step_ids in journey.items():
        unknown = set(step_ids) - set(steps)
        if unknown:
            raise GuideError(f"{source}: el recorrido «{category}» usa pasos que no existen: {sorted(unknown)}.")
    instruments = data.get("instrumentos")
    if not isinstance(instruments, list) or not instruments:
        raise GuideError(f"{source}: «instrumentos» debe ser una lista con contenido.")
    for position, item in enumerate(instruments, start=1):
        for key in ("nombre", "ejemplos", "desde", "que_es", "riesgo", "liquidez", "categorias", "capital"):
            if not isinstance(item, dict) or not item.get(key):
                raise GuideError(f"{source}: falta «{key}» en «instrumentos» número {position}.")
        if not isinstance(item.get("riesgo_bajo"), bool):
            raise GuideError(f"{source}: «riesgo_bajo» debe ser true o false en «instrumentos» número {position}.")
        source_item = item.get("fuente")
        if not isinstance(source_item, dict) or not source_item.get("url") or not source_item.get("titulo"):
            raise GuideError(f"{source}: falta «fuente» en «instrumentos» número {position}.")
    return data


@lru_cache(maxsize=16)
def _load_cached(path: str, _mtime_ns: int, parser) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as error:
        raise GuideError(f"{path}: el YAML no es válido: {error}") from error
    return parser(data, source=path)


def load_guide(path: str | os.PathLike | None = None) -> dict:
    """Load (and cache until the file changes) the guide at ``path``.

    Defaults to ``ATLAS_GUIA_IBKR_PATH`` or ``config/guia_ibkr.yaml``.
    """
    resolved = Path(path or os.environ.get(PATH_ENV_VAR) or DEFAULT_PATH)
    try:
        mtime = resolved.stat().st_mtime_ns
    except FileNotFoundError as error:
        raise GuideError(f"No se encontró la guía en {resolved}.") from error
    return _load_cached(str(resolved), mtime, parse_guide)


def load_instruments(path: str | os.PathLike | None = None) -> dict:
    """Load the options catalog (``ATLAS_INSTRUMENTOS_PATH`` or ``config/instrumentos.yaml``)."""
    resolved = Path(path or os.environ.get(INSTRUMENTS_ENV_VAR) or INSTRUMENTS_PATH)
    try:
        mtime = resolved.stat().st_mtime_ns
    except FileNotFoundError as error:
        raise GuideError(f"No se encontró el catálogo en {resolved}.") from error
    return _load_cached(str(resolved), mtime, parse_instruments)
