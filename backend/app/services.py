from __future__ import annotations
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .brokers import alpaca_paper_broker, simulator_broker
from .brokers.contracts import BrokerOrderRequest
from .config import settings
from .models import Account, AuditEvent, Candle, Execution, Order, Position, RiskEvent, SimulationEvent, Strategy, Subscription

MONEY = Decimal("0.0001")
TERMINAL = {"FILLED", "CANCELLED", "REJECTED", "RISK_REJECTED"}


def d(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def record_simulation_event(db: Session, account_id: int, event_type: str, title: str, detail: str = "", run_id: Optional[str] = None):
    db.add(SimulationEvent(account_id=account_id, run_id=run_id, event_type=event_type, title=title, detail=detail))


def seed_strategies(db: Session):
    definitions = [
        ("ma_cross", "Moving average crossover", "1m", "Follows short-term momentum after a confirmed crossover.", "On each closed 1-minute candle, buy when SMA(3) crosses above SMA(7), sell when it crosses below. Warm-up: 8 closed candles. Order size: 10. One signal per candle.", {"fast": 3, "slow": 7, "quantity": 10}),
        ("rsi_revert", "RSI mean reversion", "1m", "Trades measured short-term overextension using closed candles.", "On closed 1-minute candles, buy when RSI(6) falls below 35 and sell when it rises above 65. Warm-up: 7 closed candles. Order size: 8. One signal per candle.", {"period": 6, "lower": 35, "upper": 65, "quantity": 8}),
        ("breakout_5m", "Five-minute breakout", "5m", "Acts only when a completed five-minute close escapes its preceding range.", "Buy when a closed 5-minute candle closes above the prior 3-candle high; sell below the prior 3-candle low. Warm-up: 4 closed candles. Order size: 12. One signal per candle.", {"lookback": 3, "quantity": 12}),
    ]
    for sid, name, timeframe, description, rules, params in definitions:
        if not db.get(Strategy, sid):
            db.add(Strategy(id=sid, name=name, timeframe=timeframe, description=description, rules=rules, default_parameters=json.dumps(params)))
    db.commit()


def candle_bucket(ts: datetime, minutes: int) -> datetime:
    ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return ts.replace(minute=(ts.minute // minutes) * minutes)


def evaluate_strategy(db: Session, subscription: Subscription) -> Optional[Tuple[str, int, str]]:
    """Evaluate closed candles and emit an intention; strategies never access brokers."""
    strategy = db.get(Strategy, subscription.strategy_id)
    params = json.loads(subscription.parameters)
    candles = list(reversed(db.scalars(select(Candle).where(
        Candle.symbol == subscription.symbol,
        Candle.timeframe == strategy.timeframe,
        Candle.closed.is_(True),
    ).order_by(Candle.bucket_start.desc()).limit(30)).all()))
    if not candles:
        return None
    signal_key = f"{strategy.id}:{candles[-1].bucket_start.isoformat()}"
    if subscription.last_signal_key == signal_key:
        return None
    closes = [d(c.close) for c in candles]
    side = None
    if strategy.id == "ma_cross" and len(closes) >= params["slow"] + 1:
        fast, slow = params["fast"], params["slow"]
        fast_prev, slow_prev = sum(closes[-fast-1:-1]) / fast, sum(closes[-slow-1:-1]) / slow
        fast_now, slow_now = sum(closes[-fast:]) / fast, sum(closes[-slow:]) / slow
        if fast_prev <= slow_prev and fast_now > slow_now: side = "BUY"
        if fast_prev >= slow_prev and fast_now < slow_now: side = "SELL"
    elif strategy.id == "rsi_revert" and len(closes) >= params["period"] + 1:
        changes = [closes[i] - closes[i-1] for i in range(len(closes)-params["period"], len(closes))]
        gains = sum(max(c, Decimal("0")) for c in changes) / params["period"]
        losses = sum(max(-c, Decimal("0")) for c in changes) / params["period"]
        rsi = Decimal("100") if losses == 0 else Decimal("100") - Decimal("100") / (Decimal("1") + gains / losses)
        if rsi < params["lower"]: side = "BUY"
        if rsi > params["upper"]: side = "SELL"
    elif strategy.id == "breakout_5m" and len(candles) >= params["lookback"] + 1:
        prior = candles[-params["lookback"]-1:-1]
        if closes[-1] > max(d(c.high) for c in prior): side = "BUY"
        if closes[-1] < min(d(c.low) for c in prior): side = "SELL"
    if side:
        subscription.last_signal_key = signal_key
        db.commit()
        return side, int(params["quantity"]), signal_key
    return None


def ingest_tick(db: Session, symbol: str, price: Decimal, ts: datetime, quantity: Optional[int] = None):
    """UTC aligned incremental OHLC. A later bucket closes earlier open candles; late ticks never revise closed candles."""
    price = d(price)
    output = []
    for minutes, label in ((1, "1m"), (5, "5m")):
        bucket = candle_bucket(ts, minutes)
        latest = db.scalar(select(Candle).where(Candle.symbol == symbol, Candle.timeframe == label).order_by(Candle.bucket_start.desc()))
        if latest and bucket > latest.bucket_start.replace(tzinfo=timezone.utc):
            latest.closed = True
        candle = db.scalar(select(Candle).where(Candle.symbol == symbol, Candle.timeframe == label, Candle.bucket_start == bucket))
        if candle and not candle.closed:
            candle.high = max(candle.high, price)
            candle.low = min(candle.low, price)
            candle.close = price
            candle.volume = (candle.volume or 0) + quantity if quantity is not None else candle.volume
        elif candle is None and (latest is None or bucket >= latest.bucket_start.replace(tzinfo=timezone.utc)):
            candle = Candle(symbol=symbol, timeframe=label, bucket_start=bucket, open=price, high=price, low=price, close=price, volume=quantity, closed=False)
            db.add(candle)
        if candle:
            output.append(candle)
    db.commit()
    return output


def position_values(position: Position):
    unrealized = d((position.last_price - position.average_price) * position.quantity) if position.quantity else d(0)
    net = d(position.realized_pnl + unrealized - position.charges)
    return unrealized, net


def apply_fill(db: Session, order: Order, execution_id: str, qty: int, price: Decimal, charge_rate: Decimal = Decimal("0.0005")) -> bool:
    """Apply one incremental execution atomically. Unique execution IDs make replay a no-op."""
    if qty <= 0 or order.filled_qty + qty > order.requested_qty:
        raise ValueError("Invalid incremental execution quantity")
    if db.scalar(select(Execution).where(Execution.account_id == order.account_id, Execution.execution_id == execution_id)):
        return False
    price = d(price)
    charge = d(price * qty * charge_rate)
    execution = Execution(account_id=order.account_id, order_id=order.id, execution_id=execution_id, quantity=qty, price=price, charge=charge)
    db.add(execution)
    position = db.scalar(select(Position).where(Position.account_id == order.account_id, Position.subscription_id == order.subscription_id, Position.symbol == order.symbol))
    if not position:
        position = Position(account_id=order.account_id, subscription_id=order.subscription_id, symbol=order.symbol)
        db.add(position)
        db.flush()
    signed_fill = qty if order.side == "BUY" else -qty
    old_qty = position.quantity
    old_avg = d(position.average_price)
    new_qty = old_qty + signed_fill
    if old_qty == 0 or (old_qty > 0) == (signed_fill > 0):
        position.average_price = d((abs(old_qty) * old_avg + abs(signed_fill) * price) / abs(new_qty))
    else:
        closing = min(abs(old_qty), abs(signed_fill))
        direction = Decimal("1") if old_qty > 0 else Decimal("-1")
        position.realized_pnl = d(position.realized_pnl + (price - old_avg) * closing * direction)
        if new_qty == 0:
            position.average_price = d(0)
        elif (new_qty > 0) != (old_qty > 0):
            position.average_price = price
    position.quantity = new_qty
    position.last_price = price
    position.charges = d(position.charges + charge)
    previous_value = d(order.average_fill_price) * order.filled_qty
    order.filled_qty += qty
    order.reserved_qty = max(0, order.requested_qty - order.filled_qty)
    order.average_fill_price = d((previous_value + price * qty) / order.filled_qty)
    order.status = "FILLED" if order.filled_qty == order.requested_qty else "PARTIALLY_FILLED"
    order.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def reject_risk(db: Session, account_id: int, subscription_id: int, code: str, message: str, client_id: str, symbol: str, side: str, qty: int):
    db.add(RiskEvent(account_id=account_id, subscription_id=subscription_id, code=code, message=message))
    order = Order(account_id=account_id, subscription_id=subscription_id, client_order_id=client_id, symbol=symbol, side=side, requested_qty=qty, status="RISK_REJECTED", reason=message)
    db.add(order)
    db.commit()
    return order


def submit_order(db: Session, account: Account, subscription: Subscription, side: str, qty: int, price: Decimal, scenario: str = "full", client_id: Optional[str] = None, close_only: bool = False) -> Order:
    client_id = client_id or f"sim-{uuid.uuid4().hex}"
    existing = db.scalar(select(Order).where(Order.account_id == account.id, Order.client_order_id == client_id))
    if existing:
        return existing
    side = side.upper()
    if side not in {"BUY", "SELL"} or qty <= 0:
        return reject_risk(db, account.id, subscription.id, "INVALID_ORDER", "Side or quantity is invalid", client_id, subscription.symbol, side, qty)
    if not close_only:
        if account.kill_state != "RUNNING" or not account.recovered:
            return reject_risk(db, account.id, subscription.id, "ACCOUNT_BLOCKED", "Account is not recovered or kill mode is active", client_id, subscription.symbol, side, qty)
        if subscription.status != "RUNNING":
            return reject_risk(db, account.id, subscription.id, "STRATEGY_PAUSED", "Strategy is not running", client_id, subscription.symbol, side, qty)
        position = db.scalar(select(Position).where(Position.subscription_id == subscription.id, Position.symbol == subscription.symbol))
        current = position.quantity if position else 0
        pending = db.scalar(select(func.coalesce(func.sum(Order.reserved_qty), 0)).where(Order.subscription_id == subscription.id, Order.status.in_(["RISK_APPROVED", "SUBMITTING", "ACKNOWLEDGED", "PARTIALLY_FILLED"]))) or 0
        proposed = abs(current) + int(pending) + qty
        if proposed > subscription.max_position_size:
            return reject_risk(db, account.id, subscription.id, "POSITION_LIMIT", f"Proposed exposure {proposed} exceeds limit {subscription.max_position_size}", client_id, subscription.symbol, side, qty)
        since = datetime.now(timezone.utc) - timedelta(seconds=60)
        count = db.scalar(select(func.count(Order.id)).where(Order.subscription_id == subscription.id, Order.created_at >= since, Order.status != "RISK_REJECTED")) or 0
        if count >= subscription.max_orders_per_minute:
            return reject_risk(db, account.id, subscription.id, "ORDER_RATE", "Rolling 60-second order limit reached", client_id, subscription.symbol, side, qty)
        if position:
            _, net = position_values(position)
            if net <= -d(subscription.max_daily_loss):
                return reject_risk(db, account.id, subscription.id, "DAILY_LOSS", "Daily net loss limit reached", client_id, subscription.symbol, side, qty)
    request = BrokerOrderRequest(client_id, subscription.symbol, side, qty)
    ack = alpaca_paper_broker.place_order(request) if settings.is_alpaca_paper else simulator_broker.place_order(request, scenario)
    order = Order(account_id=account.id, subscription_id=subscription.id, client_order_id=client_id, broker_order_id=ack.broker_order_id, symbol=subscription.symbol, side=side, requested_qty=qty, reserved_qty=qty, status=ack.status, reason=ack.reason, close_only=close_only)
    db.add(order)
    db.commit()
    db.refresh(order)
    record_simulation_event(db, account.id, "ORDER_ACK", f"{side} {qty} {subscription.symbol}", f"{scenario} scenario · {order.status}")
    if ack.status == "REJECTED":
        order.reserved_qty = 0
        db.commit()
    elif settings.is_alpaca_paper:
        record_simulation_event(db, account.id, "ORDER_ACK", f"Alpaca Paper accepted {side} {qty} {subscription.symbol}", "Awaiting Alpaca order and fill reconciliation")
        db.commit()
    else:
        for execution in simulator_broker.execution_plan(ack, qty, price, scenario):
            apply_fill(db, order, execution.execution_id, execution.incremental_quantity, execution.price)
            record_simulation_event(db, account.id, "FILL", f"{execution.incremental_quantity} {subscription.symbol} filled", f"{side} at ₹{execution.price}")
        if scenario == "partial":
            record_simulation_event(
                db,
                account.id,
                "PARTIALLY_FILLED",
                f"{order.filled_qty} of {order.requested_qty} {subscription.symbol} filled",
                f"Remaining {order.reserved_qty} quantity is pending",
            )
        if scenario == "cancel_race":
            order.status = "CANCELLED"
            order.reserved_qty = 0
            record_simulation_event(db, account.id, "CANCELLED", f"Remaining {subscription.symbol} quantity cancelled", "Cancellation raced with a confirmed partial fill")
        db.commit()
    return order


def mark_symbol(db: Session, account_id: int, symbol: str, price: Decimal):
    """Mark each strategy sub-ledger to the most recent broker trade."""
    for position in db.scalars(select(Position).where(Position.account_id == account_id, Position.symbol == symbol)).all():
        position.last_price = d(price)
    db.commit()


def reconcile_alpaca_orders(db: Session, account: Account):
    """Apply Alpaca activity fills exactly once and mirror terminal order states."""
    orders = db.scalars(select(Order).where(
        Order.account_id == account.id,
        Order.broker_order_id.is_not(None),
        or_(
            Order.status.not_in(TERMINAL),
            (Order.status == "FILLED") & (Order.filled_qty < Order.requested_qty),
        ),
    )).all()
    for order in orders:
        try:
            snapshot = alpaca_paper_broker.order(order.broker_order_id)
            for fill in alpaca_paper_broker.fills(order.broker_order_id):
                # Alpaca reports commission-free paper fills. Broker fees are not invented.
                apply_fill(db, order, fill.execution_id, fill.quantity, fill.price, Decimal("0"))
            db.refresh(order)
            if snapshot.filled_qty > order.filled_qty:
                # Activities are the preferred exact execution source. Alpaca's
                # order snapshot is a safe fallback if the activity feed lags.
                fallback_qty = snapshot.filled_qty - order.filled_qty
                fallback_price = snapshot.average_fill_price or d(order.average_fill_price)
                apply_fill(
                    db,
                    order,
                    f"{order.broker_order_id}-snapshot-{snapshot.filled_qty}",
                    fallback_qty,
                    fallback_price,
                    Decimal("0"),
                )
                db.refresh(order)
            order.status = snapshot.status
            order.reason = snapshot.reason
            if snapshot.status in TERMINAL:
                order.reserved_qty = 0
            elif snapshot.status == "PARTIALLY_FILLED":
                order.reserved_qty = max(0, order.requested_qty - order.filled_qty)
            db.commit()
        except RuntimeError as error:
            # Keep the order open for the next reconciliation attempt; do not
            # fabricate a broker rejection from a transient connectivity issue.
            order.reason = str(error)
            db.commit()


def aggregate_account(db: Session, account_id: int):
    positions = db.scalars(select(Position).where(Position.account_id == account_id)).all()
    by_symbol: dict[str, int] = {}
    realized = unrealized = charges = Decimal("0")
    for p in positions:
        by_symbol[p.symbol] = by_symbol.get(p.symbol, 0) + p.quantity
        u, _ = position_values(p)
        realized += p.realized_pnl
        unrealized += u
        charges += p.charges
    return {"positions": by_symbol, "realized_pnl": d(realized), "unrealized_pnl": d(unrealized), "charges": d(charges), "net_pnl": d(realized + unrealized - charges)}


def perform_kill(db: Session, account: Account, actor_id: int):
    if account.kill_state == "HALTED":
        return account
    account.kill_state = "KILL_REQUESTED"
    for sub in db.scalars(select(Subscription).where(Subscription.account_id == account.id)).all():
        sub.status = "PAUSED"
    db.commit()
    account.kill_state = "CANCELLING"
    for order in db.scalars(select(Order).where(Order.account_id == account.id, Order.status.in_(["ACKNOWLEDGED", "PARTIALLY_FILLED", "SUBMITTING", "UNKNOWN"]))).all():
        # Historical simulator orders have SIM-* identifiers and never existed
        # at Alpaca. Cancel those locally when a workspace migrates to Paper.
        if settings.is_alpaca_paper and order.broker_order_id and not order.broker_order_id.startswith("SIM-"):
            acknowledgement = alpaca_paper_broker.cancel_order(order.broker_order_id)
            order.status = acknowledgement.status
            order.reason = acknowledgement.reason
        else:
            order.status = "CANCELLED"
        if order.status == "CANCELLED":
            order.reserved_qty = 0
    db.commit()
    account.kill_state = "CLOSING"
    db.commit()
    for position in db.scalars(select(Position).where(Position.account_id == account.id, Position.quantity != 0)).all():
        sub = db.get(Subscription, position.subscription_id)
        if settings.is_alpaca_paper and position.symbol == "RELIANCE":
            # RELIANCE only existed in the retired deterministic simulator. It
            # must be removed from the local ledger, never submitted to Alpaca.
            position.quantity = 0
            position.average_price = d(0)
            record_simulation_event(db, account.id, "MIGRATION", "Retired simulator RELIANCE position", "No Alpaca order was sent")
            db.commit()
            continue
        side = "SELL" if position.quantity > 0 else "BUY"
        submit_order(db, account, sub, side, abs(position.quantity), position.last_price, client_id=f"kill-{account.id}-{position.id}", close_only=True)
    remaining = db.scalar(select(func.count(Position.id)).where(Position.account_id == account.id, Position.quantity != 0)) or 0
    account.kill_state = "HALTED" if remaining == 0 else "NEEDS_ATTENTION"
    db.add(AuditEvent(account_id=account.id, actor_user_id=actor_id, action="KILL_SWITCH", detail=f"Completed with {remaining} open strategy positions"))
    db.commit()
    return account
