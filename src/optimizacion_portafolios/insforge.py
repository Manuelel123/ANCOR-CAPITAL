"""Minimal InsForge REST client (database only) for the server-side price cache.

User accounts are local (``storage.models.User``); InsForge only hosts the
persistent price tables used by ``data.price_store``. Every call uses the
project API key, a full-access admin key that never leaves the server.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
# PostgREST caps responses; reads page through results in chunks of this size.
PAGE_SIZE = 1000


class InsForgeError(Exception):
    """An InsForge request failed (HTTP error, network error or bad response)."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class InsForgeSettings:
    base_url: str
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> "InsForgeSettings | None":
        base_url = os.environ.get("INSFORGE_URL", "").strip()
        api_key = os.environ.get("INSFORGE_API_KEY", "").strip() or None
        if not base_url or not api_key:
            return None
        return cls(base_url.rstrip("/"), api_key)


def in_filter(values) -> str:
    """PostgREST ``in.(...)`` filter with each value double-quoted."""
    quoted = ",".join('"' + str(value).replace('"', '\\"') + '"' for value in values)
    return f"in.({quoted})"


class InsForgeClient:
    def __init__(
        self,
        settings: InsForgeSettings,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ):
        self.settings = settings
        self.timeout = timeout
        self._http = session or requests.Session()

    @property
    def has_admin_key(self) -> bool:
        return bool(self.settings.api_key)

    # ------------------------------------------------------------------ HTTP
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        headers: dict | None = None,
        deadline: float | None = None,
    ) -> Any:
        if not self.settings.api_key:
            raise InsForgeError("Falta INSFORGE_API_KEY para operaciones del servidor.")
        # ``deadline`` is a ``time.monotonic()`` value shared by several calls.
        timeout = self.timeout
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0:
                raise InsForgeError("Se agotó el tiempo de espera de InsForge.")
        request_headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        if json is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        try:
            response = self._http.request(
                method,
                self.settings.base_url + path,
                params=params,
                json=json,
                headers=request_headers,
                timeout=timeout,
            )
        except requests.RequestException as error:
            raise InsForgeError(str(error)) from error
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            message = body.get("message") or body.get("error") or response.reason
            raise InsForgeError(str(message), response.status_code, body.get("error"))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise InsForgeError("Respuesta no válida del servidor.", response.status_code) from error

    # -------------------------------------------------------------- Database
    # ``admin`` is kept for the callers' readability: every call uses the API key.
    def select(
        self,
        table: str,
        params: dict | None = None,
        *,
        admin: bool = True,
        limit: int | None = None,
        deadline: float | None = None,
    ) -> list[dict]:
        """Rows of ``table`` matching PostgREST ``params``, paging past the cap."""
        rows: list[dict] = []
        offset = 0
        while True:
            page = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - len(rows))
            query = {**(params or {}), "limit": page, "offset": offset}
            batch = self._request(
                "GET", f"/api/database/records/{table}", params=query, deadline=deadline
            ) or []
            rows.extend(batch)
            if len(batch) < page or (limit is not None and len(rows) >= limit):
                return rows
            offset += page

    def insert(
        self,
        table: str,
        rows: list[dict],
        *,
        admin: bool = True,
        upsert: bool = False,
        on_conflict: str | None = None,
        returning: bool = True,
        deadline: float | None = None,
    ) -> list[dict]:
        if not rows:
            return []
        prefer = ["return=representation" if returning else "return=minimal"]
        if upsert:
            prefer.append("resolution=merge-duplicates")
        params = {"on_conflict": on_conflict} if on_conflict else None
        result = self._request(
            "POST",
            f"/api/database/records/{table}",
            params=params,
            json=rows,
            headers={"Prefer": ",".join(prefer)},
            deadline=deadline,
        )
        return result or []

    def delete(self, table: str, params: dict, *, admin: bool = True) -> list[dict]:
        if not params:
            raise ValueError("delete() requires a filter.")
        return self._request(
            "DELETE",
            f"/api/database/records/{table}",
            params=params,
            headers={"Prefer": "return=representation"},
        ) or []
