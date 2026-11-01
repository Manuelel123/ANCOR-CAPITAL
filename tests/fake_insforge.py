"""In-memory stand-in for ``InsForgeClient`` (no network) used by the tests."""

import re
import time

from optimizacion_portafolios.insforge import InsForgeError, InsForgeSettings


def _matches(row: dict, params: dict) -> bool:
    for key, condition in params.items():
        if key in {"select", "order", "limit", "offset", "on_conflict"}:
            continue
        if key == "and":
            for clause in condition.strip("()").split(","):
                column, operator, value = clause.split(".", 2)
                cell = str(row.get(column))
                if operator == "gte" and not cell >= value:
                    return False
                if operator == "lte" and not cell <= value:
                    return False
            continue
        operator, value = condition.split(".", 1)
        if operator == "eq" and str(row.get(key)) != value:
            return False
        if operator == "in":
            values = re.findall(r'"((?:[^"\\]|\\.)*)"', value)
            if str(row.get(key)) not in values:
                return False
    return True


class FakeInsForge:
    """Database methods of ``InsForgeClient`` over dict tables (admin key only)."""

    def __init__(self, admin_key: str | None = "admin-key"):
        self.settings = InsForgeSettings("https://fake.insforge.app", admin_key)
        self.tables: dict[str, list[dict]] = {}
        self.calls: list[tuple] = []

    @property
    def has_admin_key(self) -> bool:
        return bool(self.settings.api_key)

    # ------------------------------------------------------------ database
    def _check_key(self, deadline=None):
        if not self.settings.api_key:
            raise InsForgeError("no admin key")
        if deadline is not None and time.monotonic() >= deadline:
            raise InsForgeError("timeout")

    def _rows(self, table):
        return self.tables.setdefault(table, [])

    def select(self, table, params=None, *, admin=True, limit=None, deadline=None):
        self._check_key(deadline)
        self.calls.append(("select", table))
        rows = [dict(row) for row in self._rows(table) if _matches(row, params or {})]
        return rows[:limit] if limit is not None else rows

    def insert(
        self, table, rows, *, admin=True, upsert=False, on_conflict=None, returning=True, deadline=None
    ):
        self._check_key(deadline)
        self.calls.append(("insert", table, len(rows)))
        stored = self.tables.setdefault(table, [])
        created = []
        keys = on_conflict.split(",") if on_conflict else None
        for row in rows:
            row = dict(row)
            if upsert and keys:
                existing = next(
                    (old for old in stored if all(old.get(key) == row.get(key) for key in keys)),
                    None,
                )
                if existing is not None:
                    existing.update(row)
                    created.append(dict(existing))
                    continue
            stored.append(row)
            created.append(dict(row))
        return created if returning else []

    def delete(self, table, params, *, admin=True):
        self._check_key()
        doomed = [row for row in self._rows(table) if _matches(row, params)]
        self.tables[table] = [row for row in self._rows(table) if row not in doomed]
        return doomed
