"""Tables of local users, risk profiles, the decision log, HRP backtest history and tracked ("optimal") portfolios.

``DailySnapshot`` keeps one row per portfolio and business day (value,
benchmark value, weights and, for the days it was computed, the metrics), so
the history survives later changes in the provider's data.

A ``Portfolio`` is a sequence of ``PortfolioVersion`` rows: fixing the
portfolio creates version 1, and every rebalance, removal or addition creates
the next one with its ``Holding`` rows (target weight, units, entry price) and
the ``Transaction`` rows that turned the previous holdings into the new ones.
The current version is the one with the highest ``number`` (no pointer column,
so there is no circular foreign key to keep in sync).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from flask_login import UserMixin
from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false, text, true
from sqlalchemy import event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from optimizacion_portafolios.storage import db

# Access tiers, lowest to highest. "gestor" is only granted by `flask users make-gestor`.
ROLES = ("novato", "inversionista", "gestor")
DEFAULT_ROLE = "novato"
DEFAULT_PLAN = "piloto"
MODELS = ("hrp", "black_litterman")
VERSION_REASONS = ("fix", "rebalance", "reoptimize", "remove", "add")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class User(UserMixin, db.Model):
    """Local account: the email is the username; the password is an argon2 hash.

    Rows owned by a user (portfolios, backtests) store ``str(user.id)`` in
    their ``user_id`` column.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=DEFAULT_ROLE, server_default=DEFAULT_ROLE)
    role_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Commercial plan; stored for later, not enforced yet.
    plan: Mapped[str] = mapped_column(String(20), default=DEFAULT_PLAN, server_default=DEFAULT_PLAN)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when the owner proves the email (verification or reset code).
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Data notice: when it was accepted and which version (config/avisos.yaml).
    datos_aceptados_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    datos_aviso_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    intro_vista_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def name(self) -> str:
        return self.display_name or self.email.split("@")[0]

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    def __repr__(self) -> str:  # never includes the password hash
        return f"<User {self.id} {self.email} {self.role}>"


class EmailCode(db.Model):
    """One-time 6-digit code sent by email (account verification or password reset).

    Only a keyed hash of the code is stored. A new code of the same purpose
    replaces the previous one; a code expires, is used once, and is burned
    after too many wrong attempts.
    """

    __tablename__ = "email_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(10))  # "verify" | "reset"
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BacktestRun(db.Model):
    """One HRP backtest of a user, stored once per distinct inputs (fingerprint)."""

    __tablename__ = "backtest_runs"
    __table_args__ = (UniqueConstraint("user_id", "fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    tickers: Mapped[list] = mapped_column(JSON, default=list)
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    trading_days: Mapped[int] = mapped_column(Integer)
    lookback: Mapped[int] = mapped_column(Integer)
    drift_threshold: Mapped[float] = mapped_column(Float)
    vol_window: Mapped[int] = mapped_column(Integer)
    vol_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float)
    slippage: Mapped[float] = mapped_column(Float)
    init_cash: Mapped[float] = mapped_column(Float)
    best_strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    results: Mapped[list[BacktestResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="BacktestResult.id"
    )


class BacktestResult(db.Model):
    """Metrics of one rebalancing strategy within a ``BacktestRun``."""

    __tablename__ = "backtest_results"
    __table_args__ = (UniqueConstraint("run_id", "strategy"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(64))
    total_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    sortino: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    rebalances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_costs: Mapped[float | None] = mapped_column(Float, nullable=True)
    rebalance_dates: Mapped[list] = mapped_column(JSON, default=list)

    run: Mapped[BacktestRun] = relationship(back_populates="results")


class Portfolio(db.Model):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120))
    model: Mapped[str] = mapped_column(String(20))
    benchmark: Mapped[str] = mapped_column(String(32))
    initial_capital: Mapped[float] = mapped_column(Float)
    commission_bps: Mapped[float] = mapped_column(Float, default=10.0)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    tracking_start: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A gestor's portfolio shown, as a summary, on the home page of other users.
    visible_usuarios: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    versions: Mapped[list[PortfolioVersion]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="PortfolioVersion.number",
    )
    alert_rules: Mapped[list[AlertRule]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan", order_by="AlertRule.id"
    )
    alert_events: Mapped[list[AlertEvent]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="AlertEvent.triggered_on.desc()",
        lazy="dynamic",
    )
    snapshots: Mapped[list[DailySnapshot]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="DailySnapshot.day",
        lazy="dynamic",
    )

    @property
    def current_version(self) -> PortfolioVersion | None:
        return self.versions[-1] if self.versions else None

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class PortfolioVersion(db.Model):
    __tablename__ = "portfolio_versions"
    __table_args__ = (UniqueConstraint("portfolio_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    effective_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Model inputs (universe, window, weight limits, views, objective, rf...).
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    # Portfolio value just before and after the change (after = before - cost).
    value_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_after: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    turnover: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    portfolio: Mapped[Portfolio] = relationship(back_populates="versions")
    holdings: Mapped[list[Holding]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="Holding.target_weight.desc()",
    )
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )

    @property
    def target_weights(self) -> dict[str, float]:
        return {holding.ticker: holding.target_weight for holding in self.holdings}

    @property
    def units(self) -> dict[str, float]:
        return {holding.ticker: holding.units for holding in self.holdings}


class Holding(db.Model):
    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("version_id", "ticker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("portfolio_versions.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(32))
    target_weight: Mapped[float] = mapped_column(Float)
    units: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)

    version: Mapped[PortfolioVersion] = relationship(back_populates="holdings")


class Transaction(db.Model):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("portfolio_versions.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))  # "buy" or "sell"
    units: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float, default=0.0)

    version: Mapped[PortfolioVersion] = relationship(back_populates="transactions")


class DailySnapshot(db.Model):
    __tablename__ = "daily_snapshots"
    __table_args__ = (UniqueConstraint("portfolio_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    benchmark_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    # Scalar metrics as of ``day``; None for days backfilled without them.
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    portfolio: Mapped[Portfolio] = relationship(back_populates="snapshots")


class AlertRule(db.Model):
    """User-editable thresholds of one alert kind (see ``analytics.alerts``)."""

    __tablename__ = "alert_rules"
    __table_args__ = (UniqueConstraint("portfolio_id", "kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    yellow: Mapped[float | None] = mapped_column(Float, nullable=True)
    red: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    portfolio: Mapped[Portfolio] = relationship(back_populates="alert_rules")


class AlertEvent(db.Model):
    """A period during which a rule stayed at one level (open while unresolved)."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(8))
    triggered_on: Mapped[date] = mapped_column(Date)
    resolved_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    updated_on: Mapped[date] = mapped_column(Date)

    portfolio: Mapped[Portfolio] = relationship(back_populates="alert_events")

    @property
    def is_open(self) -> bool:
        return self.resolved_on is None


class Profile(db.Model):
    """One completed risk questionnaire of a user (never overwritten).

    Repeating the questionnaire adds a row and marks only that one as
    ``vigente`` (``storage.profiles.save_profile``). Column names follow the
    questionnaire's vocabulary (Spanish). Levels are the ids of
    ``config/cuestionario.yaml`` (``adverso``, ``moderado``, ``arriesgado``);
    ``nivel_horizonte`` is the horizon cap (the highest level when uncapped).
    """

    __tablename__ = "perfiles"
    __table_args__ = (
        # At most one current profile per user.
        Index(
            "uq_perfiles_vigente",
            "usuario_id",
            unique=True,
            sqlite_where=text("vigente = 1"),
            postgresql_where=text("vigente"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    version_cuestionario: Mapped[str] = mapped_column(String(20))
    capital_rango: Mapped[str] = mapped_column(String(32))
    horizonte: Mapped[str] = mapped_column(String(32))
    respuestas: Mapped[dict] = mapped_column(JSON, default=dict)
    puntaje_tolerancia: Mapped[float] = mapped_column(Float)
    puntaje_capacidad: Mapped[float] = mapped_column(Float)
    nivel_tolerancia: Mapped[str] = mapped_column(String(20))
    nivel_capacidad: Mapped[str] = mapped_column(String(20))
    nivel_horizonte: Mapped[str] = mapped_column(String(20))
    perfil_final: Mapped[str] = mapped_column(String(20))
    factor_limitante: Mapped[str] = mapped_column(String(20))
    rol_sugerido: Mapped[str] = mapped_column(String(20))
    vigente: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())


class ProfileDraft(db.Model):
    """Unfinished questionnaire of a user (one per user), resumed on the next visit."""

    __tablename__ = "perfil_borradores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    version_cuestionario: Mapped[str] = mapped_column(String(20))
    # Index of the next step to answer (0 = prior data).
    paso: Mapped[int] = mapped_column(Integer, default=0)
    # {"capital_rango": ..., "horizonte": ..., "respuestas": {question id: 1..4}}
    datos: Mapped[dict] = mapped_column(JSON, default=dict)
    actualizado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


DECISION_METHODS = ("hrp", "black_litterman")
# Where the portfolio was generated: a page, the comparison or "fijar".
DECISION_SOURCES = ("hrp", "black_litterman", "comparar", "fijar")


class DecisionRecord(db.Model):
    """One generated portfolio, kept as a verifiable, append-only history.

    A row is inserted every time HRP or Black-Litterman produces a portfolio
    (one row per method in the comparison) and when a portfolio is fixed.
    It stores the user's current profile at that moment (id and
    ``perfil_final``), the method, the weights and the relevant inputs. Rows
    are never updated or deleted by the application: the ORM refuses it (see
    the listeners below) and ``storage.decisions`` only inserts and reads.
    """

    __tablename__ = "registro_decisiones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # SET NULL only matters if a profile row were ever removed; perfil_final stays.
    perfil_id: Mapped[int | None] = mapped_column(ForeignKey("perfiles.id", ondelete="SET NULL"), nullable=True)
    perfil_final: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rol: Mapped[str] = mapped_column(String(20))
    metodo: Mapped[str] = mapped_column(String(20))
    origen: Mapped[str] = mapped_column(String(20))
    # {ticker: weight} (fractions that add up to 1).
    activos: Mapped[dict] = mapped_column(JSON, default=dict)
    # Inputs that produced the weights (universe, dates, limits, views...).
    parametros: Mapped[dict] = mapped_column(JSON, default=dict)


class AppendOnlyError(RuntimeError):
    """Raised when code tries to change or delete a decision record."""


def _refuse_change(_mapper, _connection, target) -> None:
    raise AppendOnlyError(f"El registro de decisiones {target.id} no se puede modificar ni borrar.")


event.listen(DecisionRecord, "before_update", _refuse_change)
event.listen(DecisionRecord, "before_delete", _refuse_change)
