"""Owner-scoped access to tracked portfolios.

Every read takes the ``user_id`` (``str(User.id)``) of the signed-in user, so a route
can never load another user's portfolio: it simply gets ``None`` (a 404).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import (
    AlertEvent,
    AlertRule,
    DailySnapshot,
    Holding,
    MODELS,
    Portfolio,
    PortfolioVersion,
    Transaction,
    User,
    VERSION_REASONS,
)


@dataclass(frozen=True)
class HoldingData:
    ticker: str
    target_weight: float
    units: float
    entry_price: float


@dataclass(frozen=True)
class TransactionData:
    ticker: str
    side: str
    units: float
    price: float
    amount: float
    cost: float = 0.0


def list_portfolios(user_id: str, include_archived: bool = True) -> list[Portfolio]:
    query = select(Portfolio).where(Portfolio.user_id == user_id)
    if not include_archived:
        query = query.where(Portfolio.status == "active")
    query = query.order_by(Portfolio.status, Portfolio.created_at.desc())
    return list(db.session.scalars(query))


def get_portfolio(user_id: str, portfolio_id: int) -> Portfolio | None:
    """The portfolio if it exists and belongs to ``user_id``."""
    return db.session.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
    )


def active_portfolios() -> list[Portfolio]:
    """Every active portfolio of every user (for the daily snapshot command)."""
    return list(db.session.scalars(select(Portfolio).where(Portfolio.status == "active")))


def _version(
    number: int,
    effective_date: date,
    reason: str,
    holdings: list[HoldingData],
    transactions: list[TransactionData],
    *,
    params: dict | None,
    value_before: float | None,
    value_after: float,
    cost: float,
    turnover: float,
    note: str | None,
) -> PortfolioVersion:
    if reason not in VERSION_REASONS:
        raise ValueError(f"Unknown version reason: {reason}")
    if not holdings:
        raise ValueError("A portfolio version needs at least one holding.")
    return PortfolioVersion(
        number=number,
        effective_date=effective_date,
        reason=reason,
        note=note,
        params=params or {},
        value_before=value_before,
        value_after=value_after,
        cost=cost,
        turnover=turnover,
        holdings=[Holding(**vars(holding)) for holding in holdings],
        transactions=[Transaction(**vars(transaction)) for transaction in transactions],
    )


def create_portfolio(
    user_id: str,
    *,
    name: str,
    model: str,
    benchmark: str,
    initial_capital: float,
    tracking_start: date,
    holdings: list[HoldingData],
    transactions: list[TransactionData] = (),
    params: dict | None = None,
    commission_bps: float = 10.0,
    cost: float = 0.0,
) -> Portfolio:
    """Create a portfolio and its version 1 (the fixing) in one commit."""
    if model not in MODELS:
        raise ValueError(f"Unknown model: {model}")
    portfolio = Portfolio(
        user_id=user_id,
        name=name,
        model=model,
        benchmark=benchmark,
        initial_capital=initial_capital,
        commission_bps=commission_bps,
        tracking_start=tracking_start,
        versions=[
            _version(
                1, tracking_start, "fix", list(holdings), list(transactions),
                params=params, value_before=None, value_after=initial_capital - cost,
                cost=cost, turnover=1.0, note=None,
            )
        ],
    )
    db.session.add(portfolio)
    db.session.commit()
    return portfolio


def add_version(
    portfolio: Portfolio,
    *,
    effective_date: date,
    reason: str,
    holdings: list[HoldingData],
    transactions: list[TransactionData],
    value_before: float,
    value_after: float,
    cost: float,
    turnover: float,
    params: dict | None = None,
    note: str | None = None,
) -> PortfolioVersion:
    current = portfolio.current_version
    if current is not None and effective_date < current.effective_date:
        raise ValueError("A new version cannot start before the current one.")
    version = _version(
        (current.number if current else 0) + 1, effective_date, reason,
        list(holdings), list(transactions), params=params,
        value_before=value_before, value_after=value_after, cost=cost,
        turnover=turnover, note=note,
    )
    portfolio.versions.append(version)
    db.session.commit()
    return version


def archive_portfolio(portfolio: Portfolio, when) -> None:
    portfolio.status = "archived"
    portfolio.archived_at = when
    db.session.commit()


def rename_portfolio(portfolio: Portfolio, name: str) -> None:
    portfolio.name = name
    db.session.commit()


def set_portfolio_visibility(portfolio: Portfolio, visible: bool) -> None:
    portfolio.visible_usuarios = visible
    db.session.commit()


def delete_portfolio(portfolio: Portfolio) -> None:
    """Remove the portfolio with its versions, valuations and alerts."""
    db.session.delete(portfolio)
    db.session.commit()


def visible_gestor_portfolios() -> list[Portfolio]:
    """Active portfolios a gestor chose to show to the other users."""
    gestores = [str(user_id) for user_id in db.session.scalars(select(User.id).where(User.role == "gestor"))]
    query = select(Portfolio).where(
        Portfolio.user_id.in_(gestores), Portfolio.visible_usuarios.is_(True), Portfolio.status == "active"
    )
    return list(db.session.scalars(query.order_by(Portfolio.name)))


def visible_gestor_portfolio(portfolio_id: int) -> Portfolio | None:
    """The portfolio if a gestor shows it to the other users."""
    return next((p for p in visible_gestor_portfolios() if p.id == portfolio_id), None)


def save_snapshots(portfolio: Portfolio, rows: list[dict], refresh_day: date | None = None) -> int:
    """Insert the snapshots of new days; only ``refresh_day`` may be overwritten.

    Older snapshots are history and never change, even if the provider later
    revises its prices. Returns the number of rows written.
    """
    portfolio_id = portfolio.id
    try:
        return _write_snapshots(portfolio_id, rows, refresh_day)
    except IntegrityError:
        # Another request (e.g. the same page opened twice) stored one of these
        # days between our read and our commit: retry against what is there now.
        db.session.rollback()
        return _write_snapshots(portfolio_id, rows, refresh_day)


def _stored_snapshots(portfolio_id: int) -> dict[date, DailySnapshot]:
    return {
        snapshot.day: snapshot
        for snapshot in db.session.scalars(
            select(DailySnapshot).where(DailySnapshot.portfolio_id == portfolio_id)
        )
    }


def _write_snapshots(portfolio_id: int, rows: list[dict], refresh_day: date | None) -> int:
    existing = _stored_snapshots(portfolio_id)
    written = 0
    for row in rows:
        stored = existing.get(row["day"])
        if stored is None:
            db.session.add(DailySnapshot(portfolio_id=portfolio_id, **row))
            written += 1
        elif row["day"] == refresh_day:
            for key, value in row.items():
                setattr(stored, key, value)
            written += 1
    db.session.commit()
    return written


def latest_snapshots(portfolio: Portfolio, limit: int = 10) -> list[DailySnapshot]:
    return list(
        db.session.scalars(
            select(DailySnapshot)
            .where(DailySnapshot.portfolio_id == portfolio.id)
            .order_by(DailySnapshot.day.desc())
            .limit(limit)
        )
    )


# -------------------------------------------------------------------- alerts
def set_alert_rules(portfolio: Portfolio, rules: dict[str, tuple[float | None, float | None, bool]]) -> None:
    """Create or update the rules ``kind -> (yellow, red, enabled)``."""
    existing = {rule.kind: rule for rule in portfolio.alert_rules}
    for kind, (yellow, red, enabled) in rules.items():
        rule = existing.get(kind)
        if rule is None:
            portfolio.alert_rules.append(AlertRule(kind=kind, yellow=yellow, red=red, enabled=enabled))
        else:
            rule.yellow, rule.red, rule.enabled = yellow, red, enabled
    db.session.commit()


def open_alerts(portfolio: Portfolio) -> list[AlertEvent]:
    return list(
        db.session.scalars(
            select(AlertEvent)
            .where(AlertEvent.portfolio_id == portfolio.id, AlertEvent.resolved_on.is_(None))
            .order_by(AlertEvent.triggered_on.desc())
        )
    )


def alert_history(portfolio: Portfolio, limit: int = 100) -> list[AlertEvent]:
    return list(
        db.session.scalars(
            select(AlertEvent)
            .where(AlertEvent.portfolio_id == portfolio.id)
            .order_by(AlertEvent.triggered_on.desc(), AlertEvent.id.desc())
            .limit(limit)
        )
    )


def sync_alerts(portfolio: Portfolio, signals, as_of: date) -> list[AlertEvent]:
    """Open, update or resolve events from today's signals; returns open events.

    A rule keeps one open event per level: a level change resolves the open
    event on ``as_of`` and opens a new one; green resolves it. Evaluations of
    an earlier day than an open event's last update are ignored.
    """
    open_by_kind = {event.kind: event for event in open_alerts(portfolio)}
    for signal in signals:
        event = open_by_kind.get(signal.kind)
        if event is not None and as_of < event.updated_on:
            continue
        if event is not None and (signal.level == "green" or signal.level != event.level):
            event.resolved_on = as_of
            event = None
        if signal.level == "green":
            continue
        if event is None:
            db.session.add(
                AlertEvent(
                    portfolio_id=portfolio.id, kind=signal.kind, level=signal.level,
                    triggered_on=as_of, value=signal.value, message=signal.message, updated_on=as_of,
                )
            )
        else:
            event.value, event.message, event.updated_on = signal.value, signal.message, as_of
    # Rules that were disabled or removed resolve their open events.
    evaluated = {signal.kind for signal in signals}
    for kind, event in open_by_kind.items():
        if kind not in evaluated and event.resolved_on is None:
            event.resolved_on = as_of
    db.session.commit()
    return open_alerts(portfolio)


def open_alert_levels(user_id: str) -> dict[int, list[str]]:
    """Open alert levels per portfolio of ``user_id`` (for the list page)."""
    rows = db.session.execute(
        select(AlertEvent.portfolio_id, AlertEvent.level)
        .join(Portfolio, Portfolio.id == AlertEvent.portfolio_id)
        .where(Portfolio.user_id == user_id, AlertEvent.resolved_on.is_(None))
    )
    levels: dict[int, list[str]] = {}
    for portfolio_id, level in rows:
        levels.setdefault(portfolio_id, []).append(level)
    return levels
