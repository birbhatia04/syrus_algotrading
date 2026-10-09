"""Platform decisions and risk checks; no strategy has broker access."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from sqlalchemy import select, func
from .config import settings
from .models import Candle, Execution, Order, Position, StrategyDay

IST = timezone(timedelta(hours=5, minutes=30))
SUPPORTED = {"time_entry", "open_breakout", "ma_cross"}
OPEN_STATES = {"CREATED", "RISK_APPROVED", "SUBMITTING", "UNKNOWN", "ACKNOWLEDGED", "PARTIALLY_FILLED", "CANCEL_PENDING"}


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def day_string(now):
    return utc(now).astimezone(IST).date().isoformat()


def day_ledger(db, sub_id, now):
    day = day_string(now)
    ledger = db.scalar(select(StrategyDay).where(StrategyDay.subscription_id == sub_id, StrategyDay.trading_day == day))
    if ledger is None:
        ledger = StrategyDay(subscription_id=sub_id, trading_day=day)
        db.add(ledger)
        db.flush()
    return ledger


def sub_position(db, sub):
    return db.scalar(select(Position).where(Position.subscription_id == sub.id, Position.symbol == sub.symbol))


def daily_net(db, sub, now):
    ledger = day_ledger(db, sub.id, now)
    position = sub_position(db, sub)
    unrealized = (position.last_price - position.average_price) * position.quantity if position else Decimal(0)
    return ledger.realized_pnl + unrealized - ledger.charges


def quote_fresh(quote, now):
    if quote is None or quote.price <= 0:
        return False
    age = (utc(now) - utc(quote.market_at)).total_seconds()
    return 0 <= age <= settings.market_stale_seconds and (utc(now) - utc(quote.received_at)).total_seconds() <= settings.market_stale_seconds


def open_orders(db, sub_id):
    return db.scalars(select(Order).where(Order.subscription_id == sub_id, Order.status.in_(OPEN_STATES))).all()


def risk_reason(db, account, state, sub, instrument, quote, signed_qty, now, close_only=False):
    if sub.account_id != account.id or account.id != settings.broker_021_account_id:
        return "ACCOUNT_OWNERSHIP", "This subscription is not bound to the configured 021 account"
    if not instrument or (not close_only and instrument.trading_day != day_string(now)):
        return "INSTRUMENT", "Today's NSE instrument mapping is unavailable"
    qty = abs(signed_qty)
    if not qty or qty % instrument.lot_size or qty > instrument.freeze_quantity:
        return "QUANTITY", "Quantity must satisfy lot size and freeze quantity"
    position = sub_position(db, sub)
    current = position.quantity if position else 0
    pending = open_orders(db, sub.id)
    if close_only:
        reserved_close = sum(o.reserved_qty for o in pending if o.close_only)
        if not current or current * signed_qty >= 0 or qty > abs(current) - reserved_close:
            return "CLOSE_ONLY", "Close quantity must reduce an attributed position without crossing zero"
        if any(not o.close_only for o in pending):
            return "CANCEL_FIRST", "Confirm entry cancellations before sizing the close"
        if not account.recovered:
            return "RECONCILIATION", "Resolve order ambiguity or account position mismatch before closing"
        return None
    ledger = day_ledger(db, sub.id, now)
    if daily_net(db, sub, now) <= -sub.max_daily_loss:
        ledger.loss_latched = True
    if ledger.loss_latched:
        return "DAILY_LOSS", "Daily net loss limit reached; new exposure is blocked for the trading day"
    if account.kill_state != "RUNNING" or not account.recovered or not state.enabled or sub.status != "RUNNING":
        return "ACCOUNT_BLOCKED", "Account/strategy is paused, halted, or awaiting reconciliation"
    if not state.last_reconciled_at or (utc(now) - utc(state.last_reconciled_at)).total_seconds() > 5:
        return "RECONCILIATION", "Broker reconciliation is stale"
    if not state.market_connected or not quote_fresh(quote, now):
        return "STALE_MARKET", "Fresh market data is required for new exposure"
    local_time = utc(now).astimezone(IST).time()
    if not time(9, 15) <= local_time < time(15, 15):
        return "MARKET_SESSION", "New entries are allowed from 09:15 to 15:15 IST"
    buys = sum(o.reserved_qty for o in pending if o.side == "BUY")
    sells = sum(o.reserved_qty for o in pending if o.side == "SELL")
    buys += max(0, signed_qty)
    sells += max(0, -signed_qty)
    # Either side can fill independently, so check both extremes.
    if max(abs(current + buys), abs(current - sells)) > sub.max_position_size:
        return "POSITION_LIMIT", "Worst-case filled exposure exceeds the strategy limit"
    count = db.scalar(select(func.count(Order.id)).where(
        Order.subscription_id == sub.id, Order.created_at > utc(now) - timedelta(seconds=60),
        Order.status != "RISK_REJECTED",
    ))
    if count >= sub.max_orders_per_minute:
        return "ORDER_RATE", "Rolling 60-second order limit reached"
    return None


@dataclass(frozen=True)
class Decision:
    signed_qty: int
    key: str
    close_only: bool = False


def rounded_level(value, tick_paise):
    step = Decimal(tick_paise) / 100
    return (value / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step


def strategy_decision(db, sub, quote, instrument, now):
    """Pure intention generation; the worker enforces every risk check afterwards."""
    if sub.strategy_id not in SUPPORTED:
        return None
    ledger = day_ledger(db, sub.id, now)
    position = sub_position(db, sub)
    qty = position.quantity if position else 0
    params = json.loads(sub.parameters)
    day = day_string(now)
    local_time = utc(now).astimezone(IST).time()
    if local_time >= time(15, 15) or ledger.loss_latched:
        ledger.exiting = True
    if sub.strategy_id == "open_breakout" and qty and quote_fresh(quote, now):
        entry = position.average_price
        target = rounded_level(entry * (Decimal("1.05") if qty > 0 else Decimal("0.95")), instrument.ticksize)
        stop = rounded_level(entry * (Decimal("0.95") if qty > 0 else Decimal("1.05")), instrument.ticksize)
        if (qty > 0 and (quote.price >= target or quote.price <= stop)) or (qty < 0 and (quote.price <= target or quote.price >= stop)):
            ledger.exiting = True
    if ledger.exiting:
        # Cancellation happens even when a partial fill has not arrived yet.
        return Decision(-qty, f"exit-{day}", True)
    if open_orders(db, sub.id) or not quote_fresh(quote, now):
        return None
    quantity = int(params.get("quantity", 1))
    if sub.strategy_id == "time_entry":
        if sub.status == "RUNNING" and not qty and not ledger.entry_attempted and time(9, 15) <= local_time < time(9, 16):
            return Decision(quantity if params.get("side", "BUY") == "BUY" else -quantity, f"time-{day}")
    elif sub.strategy_id == "open_breakout":
        if sub.status == "RUNNING" and not qty and not ledger.entry_attempted and quote.day_open > 0 and day_string(quote.market_at) == day:
            if quote.price >= quote.day_open * Decimal("1.01"):
                return Decision(quantity, f"breakout-{day}")
            if quote.price <= quote.day_open * Decimal("0.99"):
                return Decision(-quantity, f"breakout-{day}")
    elif sub.strategy_id == "ma_cross":
        start = datetime.combine(utc(now).astimezone(IST).date(), time(9, 15), IST).astimezone(timezone.utc)
        entry_count = db.scalar(select(func.count(Order.id)).where(
            Order.subscription_id == sub.id,
            Order.close_only.is_(False),
            Order.created_at >= start,
            Order.status != "RISK_REJECTED",
        )) or 0
        candle = db.scalar(select(Candle).where(
            Candle.symbol == sub.symbol, Candle.timeframe == "1m", Candle.closed.is_(True),
            Candle.bucket_start >= start,
        ).order_by(Candle.bucket_start.desc()))
        five = db.scalar(select(Candle).where(Candle.symbol == sub.symbol, Candle.timeframe == "5m",
                         Candle.bucket_start >= start).order_by(Candle.bucket_start.desc()))
        if not candle or not five:
            return None
        if (utc(now) - utc(candle.bucket_start)).total_seconds() > 120:
            return None
        key = f"pulse-{utc(candle.bucket_start).isoformat()}"
        if ledger.signal_key == key:
            return None
        one_bias = (candle.close - candle.open) / candle.open if candle.open else Decimal(0)
        five_bias = (five.close - five.open) / five.open if five.open else Decimal(0)
        combined = one_bias + five_bias
        one_direction = 1 if candle.close >= candle.open else -1
        latest_fill = db.scalar(select(Execution).join(Order).where(
            Order.subscription_id == sub.id
        ).order_by(Execution.executed_at.desc()).limit(1))
        timed_exit = bool(
            qty and latest_fill and
            (utc(now) - utc(latest_fill.executed_at)).total_seconds() >= int(params.get("hold_minutes", 1)) * 60
        )
        opposite_candle = (qty > 0 and one_direction < 0) or (qty < 0 and one_direction > 0)
        if qty and (opposite_candle or timed_exit):
            return Decision(-qty, key, True)
        if sub.status == "RUNNING" and not qty and entry_count < int(params.get("max_cycles", 6)):
            return Decision(quantity if combined >= 0 else -quantity, key)
    return None
