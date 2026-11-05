"""add the gestor's portfolio fixed on 2026-11-01

Portfolios fixed on a local database do not reach the deployed one, so this
copies one of them with its version, positions, valuations, alert rules and
decision record (c7d1e9f3a2b4_gestor_portfolio.json). Migrations run at
start-up. The repository is public, so the owner is not named: it is the only
gestor of the database. With no gestor, several gestores, or a portfolio
already using that name for the gestor, nothing changes. The downgrade keeps
the portfolio, because it may hold later changes.

Revision ID: c7d1e9f3a2b4
Revises: b8938e7ff6a1
Create Date: 2026-11-04 20:00:00

"""
from datetime import date, datetime, timezone
import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7d1e9f3a2b4'
down_revision = 'b8938e7ff6a1'
branch_labels = None
depends_on = None

_DATA_FILE = Path(__file__).with_suffix('.json')
_STAMP = sa.DateTime(timezone=True)


def _table(table_name, /, **columns):
    return sa.table(table_name, *(sa.column(key, kind) for key, kind in columns.items()))


_users = _table('users', id=sa.Integer, role=sa.String)
_portfolios = _table(
    'portfolios', id=sa.Integer, user_id=sa.String, name=sa.String, model=sa.String,
    benchmark=sa.String, initial_capital=sa.Float, commission_bps=sa.Float,
    status=sa.String, tracking_start=sa.Date, created_at=_STAMP, archived_at=_STAMP,
)
_versions = _table(
    'portfolio_versions', id=sa.Integer, portfolio_id=sa.Integer, number=sa.Integer,
    effective_date=sa.Date, reason=sa.String, note=sa.Text, params=sa.JSON,
    value_before=sa.Float, value_after=sa.Float, cost=sa.Float, turnover=sa.Float,
    created_at=_STAMP,
)
_holdings = _table(
    'holdings', id=sa.Integer, version_id=sa.Integer, ticker=sa.String,
    target_weight=sa.Float, units=sa.Float, entry_price=sa.Float,
)
_transactions = _table(
    'transactions', id=sa.Integer, version_id=sa.Integer, ticker=sa.String, side=sa.String,
    units=sa.Float, price=sa.Float, amount=sa.Float, cost=sa.Float,
)
_snapshots = _table(
    'daily_snapshots', id=sa.Integer, portfolio_id=sa.Integer, day=sa.Date, value=sa.Float,
    benchmark_value=sa.Float, weights=sa.JSON, metrics=sa.JSON, created_at=_STAMP,
    updated_at=_STAMP,
)
_rules = _table(
    'alert_rules', id=sa.Integer, portfolio_id=sa.Integer, kind=sa.String, yellow=sa.Float,
    red=sa.Float, enabled=sa.Boolean,
)
_decisions = _table(
    'registro_decisiones', id=sa.Integer, usuario_id=sa.Integer, fecha=_STAMP,
    perfil_final=sa.String, rol=sa.String, metodo=sa.String, origen=sa.String,
    activos=sa.JSON, parametros=sa.JSON,
)


def _value(column, value):
    """JSON text to the column's Python type (stored stamps are naive UTC)."""
    if value is None:
        return None
    if isinstance(column.type, sa.Date):
        return date.fromisoformat(value)
    if isinstance(column.type, sa.DateTime):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return value


def _insert(bind, table, row, **parent):
    values = {key: _value(table.c[key], value) for key, value in row.items()}
    statement = table.insert().values(**values, **parent).returning(table.c.id)
    return bind.execute(statement).scalar_one()


def upgrade():
    bind = op.get_bind()
    gestores = bind.execute(sa.select(_users.c.id).where(_users.c.role == 'gestor')).scalars().all()
    if len(gestores) != 1:
        return
    owner = gestores[0]
    data = json.loads(_DATA_FILE.read_text(encoding='utf-8'))
    existing = sa.select(_portfolios.c.id).where(
        _portfolios.c.user_id == str(owner), _portfolios.c.name == data['portfolio']['name']
    )
    if bind.execute(existing).first():
        return

    portfolio_id = _insert(bind, _portfolios, data['portfolio'], user_id=str(owner))
    version_id = _insert(bind, _versions, data['version'], portfolio_id=portfolio_id)
    for row in data['holdings']:
        _insert(bind, _holdings, row, version_id=version_id)
    for row in data['transactions']:
        _insert(bind, _transactions, row, version_id=version_id)
    for row in data['snapshots']:
        _insert(bind, _snapshots, row, portfolio_id=portfolio_id)
    for row in data['alert_rules']:
        _insert(bind, _rules, row, portfolio_id=portfolio_id)
    decision = dict(data['decision'])
    decision['parametros'] = {**decision['parametros'], 'portfolio_id': portfolio_id}
    _insert(bind, _decisions, decision, usuario_id=owner)


def downgrade():
    pass
