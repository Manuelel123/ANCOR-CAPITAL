"""Notices the user accepts before using Atlas (``config/avisos.yaml``).

Pure module (no Flask). The text lives in the YAML file so it can change
without touching the code; a new ``version`` asks every user to accept again.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "avisos.yaml"
PATH_ENV_VAR = "ATLAS_AVISOS_PATH"
USES_COUNT = 3


class NoticesError(ValueError):
    """The notices file is missing or invalid."""


@dataclass(frozen=True)
class DataNotice:
    version: str
    pending_review: bool
    title: str
    authorization: str
    uses: tuple[str, ...]
    checkbox: str
    legal_notice: str


def _text(source: str, value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NoticesError(f"{source}: falta el texto de «{where}».")
    return value.strip()


def parse_notices(data, source: str = "avisos") -> DataNotice:
    """``DataNotice`` from the YAML content; ``NoticesError`` names what is wrong."""
    if not isinstance(data, dict):
        raise NoticesError(f"{source}: el archivo debe ser un mapa YAML.")
    if data.get("version") in (None, ""):
        raise NoticesError(f"{source}: falta «version».")
    pending = data.get("pendiente_revision")
    if not isinstance(pending, bool):
        raise NoticesError(f"{source}: «pendiente_revision» debe ser true o false.")
    notice = data.get("aviso_datos")
    if not isinstance(notice, dict):
        raise NoticesError(f"{source}: falta «aviso_datos».")
    uses = notice.get("usos")
    if not isinstance(uses, list) or len(uses) != USES_COUNT:
        raise NoticesError(f"{source}: «aviso_datos.usos» debe listar los {USES_COUNT} usos de los datos.")
    return DataNotice(
        version=str(data["version"]),
        pending_review=pending,
        title=_text(source, notice.get("titulo"), "aviso_datos.titulo"),
        authorization=_text(source, notice.get("autorizacion"), "aviso_datos.autorizacion"),
        uses=tuple(_text(source, use, "aviso_datos.usos") for use in uses),
        checkbox=_text(source, notice.get("casilla"), "aviso_datos.casilla"),
        legal_notice=_text(source, data.get("aviso_legal"), "aviso_legal"),
    )


@lru_cache(maxsize=8)
def _load_cached(path: str, _mtime_ns: int) -> DataNotice:
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as error:
        raise NoticesError(f"{path}: el YAML no es válido: {error}") from error
    return parse_notices(data, source=path)


def load_notices(path: str | os.PathLike | None = None) -> DataNotice:
    """Load (and cache until the file changes) the notices at ``path``.

    Defaults to ``ATLAS_AVISOS_PATH`` or ``config/avisos.yaml``.
    """
    resolved = Path(path or os.environ.get(PATH_ENV_VAR) or DEFAULT_PATH)
    try:
        mtime = resolved.stat().st_mtime_ns
    except FileNotFoundError as error:
        raise NoticesError(f"No se encontraron los avisos en {resolved}.") from error
    return _load_cached(str(resolved), mtime)
