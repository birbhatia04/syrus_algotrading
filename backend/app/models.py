from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="USER")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserProfile(Base):
    __tablename__ = "user_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(30))
    city: Mapped[str] = mapped_column(String(100))
    trading_experience: Mapped[str] = mapped_column(String(30))
    risk_profile: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), default="Primary simulator")
    status: Mapped[str] = mapped_column(String(30), default="READY")
    recovered: Mapped[bool] = mapped_column(Boolean, default=True)
    kill_state: Mapped[str] = mapped_column(String(30), default="RUNNING")
    worker_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Strategy(Base):
    __tablename__ = "strategies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    timeframe: Mapped[str] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(Text)
    rules: Mapped[str] = mapped_column(Text)
    default_parameters: Mapped[str] = mapped_column(Text, default="{}")


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("account_id", "strategy_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="PAUSED")
    symbol: Mapped[str] = mapped_column(String(20), default="RELIANCE")
    parameters: Mapped[str] = mapped_column(Text, default="{}")
    max_daily_loss: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("2500"))
    max_position_size: Mapped[int] = mapped_column(Integer, default=100)
    max_orders_per_minute: Mapped[int] = mapped_column(Integer, default=5)
    last_signal_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("account_id", "client_order_id"), Index("ix_order_account_created", "account_id", "created_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    client_order_id: Mapped[str] = mapped_column(String(80))
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))
    requested_qty: Mapped[int] = mapped_column(Integer)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    reserved_qty: Mapped[int] = mapped_column(Integer, default=0)
    average_fill_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(30), default="CREATED")
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    close_only: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (UniqueConstraint("account_id", "execution_id"), Index("ix_execution_account_time", "account_id", "executed_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    execution_id: Mapped[str] = mapped_column(String(100))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    charge: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    charge_breakdown: Mapped[str] = mapped_column(Text, default="{}")
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "subscription_id", "symbol"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    average_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    charges: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    charge_breakdown: Mapped[str] = mapped_column(Text, default="{}")
    last_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "bucket_start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    timeframe: Mapped[str] = mapped_column(String(10))
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    volume: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SimulationEvent(Base):
    __tablename__ = "simulation_events"
    __table_args__ = (Index("ix_sim_event_account_created", "account_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(160))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SimulatorSession(Base):
    __tablename__ = "simulator_sessions"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    virtual_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("2500"))
    tick_index: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Instrument(Base):
    """This release trades NSE cash only; token mappings come from the daily CSV."""
    __tablename__ = "broker_instruments"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    token: Mapped[int] = mapped_column(Integer, unique=True)
    ticksize: Mapped[int] = mapped_column(Integer)
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    freeze_quantity: Mapped[int] = mapped_column(Integer)
    lower_circuit: Mapped[int] = mapped_column(Integer)
    upper_circuit: Mapped[int] = mapped_column(Integer)
    trading_day: Mapped[str] = mapped_column(String(10))


class MarketQuote(Base):
    __tablename__ = "market_quotes"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    day_open: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    cumulative_volume: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    market_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BrokerState(Base):
    __tablename__ = "broker_states"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    market_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    orders_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_reconciled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    broker_positions: Mapped[str] = mapped_column(Text, default="[]")
    kill_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    kill_elapsed_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class OrderRoute(Base):
    __tablename__ = "order_routes"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    token: Mapped[int] = mapped_column(Integer)
    exchange: Mapped[str] = mapped_column(String(10), default="NSE")
    price_paise: Mapped[int] = mapped_column(Integer, default=0)
    broker_reference: Mapped[Optional[str]] = mapped_column(String(120), unique=True, nullable=True)
    # Snapshot taken immediately before POST; helps a human resolve an ambiguous POST.
    known_order_ids: Mapped[str] = mapped_column(Text, default="[]")
    last_cancel_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class StrategyDay(Base):
    __tablename__ = "strategy_days"
    __table_args__ = (UniqueConstraint("subscription_id", "trading_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"))
    trading_day: Mapped[str] = mapped_column(String(10))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    charges: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    loss_latched: Mapped[bool] = mapped_column(Boolean, default=False)
    entry_attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    exiting: Mapped[bool] = mapped_column(Boolean, default=False)
    # Last evaluated closed candle, persisted separately from order submission.
    signal_key: Mapped[str] = mapped_column(String(100), default="")
