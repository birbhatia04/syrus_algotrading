"""Single-writer durable execution supervisor for the 021 sandbox."""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select, update
from .brokers.broker_021 import Broker021Adapter, BrokerError
from .brokers.feed_021 import EPOCH_OFFSET
from .config import settings
from .database import SessionLocal
from .models import Account, AuditEvent, BrokerState, Execution, Instrument, MarketQuote, Order, OrderRoute, Position, RiskEvent, StrategyDay, Subscription
from .services import apply_fill, d, ingest_tick, mark_symbol, rebuild_charges, record_simulation_event
from .trading_rules import IST, OPEN_STATES, SUPPORTED, daily_net, day_ledger, day_string, open_orders, risk_reason, strategy_decision, sub_position, utc

log = logging.getLogger(__name__)


def broker_trade_time(value, now):
    """Accept documented Unix seconds and the sandbox's observed 1980-epoch seconds."""
    raw = int(value)
    direct = datetime.fromtimestamp(raw, timezone.utc)
    shifted = datetime.fromtimestamp(raw - EPOCH_OFFSET, timezone.utc)
    return shifted if abs((shifted - utc(now)).total_seconds()) < abs((direct - utc(now)).total_seconds()) else direct


def lock_account(db, account_id):
    """Serialize user controls with risk approval / durable order reservation."""
    if db.bind.dialect.name == "sqlite":
        db.execute(update(Account).where(Account.id == account_id).values(
            recovered=Account.recovered).execution_options(synchronize_session=False))
    return db.scalar(select(Account).where(Account.id == account_id).with_for_update().execution_options(populate_existing=True))


def state_for(db, account_id):
    state = db.get(BrokerState, account_id)
    if state is None:
        state = BrokerState(account_id=account_id)
        db.add(state)
        db.flush()
    return state


class TradingEngine:
    def __init__(self, broker, account_id, session_factory=SessionLocal):
        self.broker = broker
        self.account_id = account_id
        self.session_factory = session_factory
        self.remote_orders = []
        self.last_poll = 0.0
        self.next_poll = 0.0
        self.failures = 0
        self.dirty = True
        self.trade_versions = {}

    def startup(self):
        with self.session_factory() as db:
            account = db.get(Account, self.account_id)
            if not account:
                return False
            state = state_for(db, account.id)
            state.market_connected = state.orders_connected = False
            account.recovered = False
            # A crash on either side of POST has an ambiguous outcome. Never resend.
            for order in db.scalars(select(Order).where(
                Order.account_id == account.id, Order.status == "SUBMITTING"
            )).all():
                order.status = "UNKNOWN"
                order.reason = "Worker restarted during submission; bind the broker order ID after review"
            db.commit()
            return True

    async def refresh_instruments(self, now):
        with self.session_factory() as db:
            if db.scalar(select(Instrument).where(Instrument.trading_day == day_string(now))):
                return
        rows = await self.broker.instruments()
        mappings = {}
        for row in rows:
            if row["exchange"] != "NSECM" or row.get("instrument_type") != "STK":
                continue
            symbol = row["symbol"]
            # Refuse ambiguous symbol mappings rather than silently selecting a token.
            if symbol in mappings:
                mappings[symbol] = None
                continue
            mappings[symbol] = row
        with self.session_factory() as db:
            for symbol, row in mappings.items():
                if row is None:
                    continue
                lot = max(1, int(row.get("board_lot_quantity") or 1))
                values = dict(token=int(row["token"]), ticksize=int(row["ticksize"]), lot_size=lot,
                              freeze_quantity=int(row.get("freeze_quantity") or 0),
                              lower_circuit=int(row.get("lower_circuit") or 0),
                              upper_circuit=int(row.get("upper_circuit") or 0), trading_day=day_string(now))
                if values["ticksize"] <= 0 or values["freeze_quantity"] <= 0:
                    continue
                instrument = db.get(Instrument, symbol)
                if instrument is None:
                    db.add(Instrument(symbol=symbol, **values))
                else:
                    for key, value in values.items():
                        setattr(instrument, key, value)
            db.commit()

    @staticmethod
    def tick_time(stamp, now):
        """Fall back to receipt time when the sandbox publishes a frozen/implausible stamp."""
        stamp = utc(stamp)
        if abs((stamp - utc(now)).total_seconds()) > settings.tick_time_tolerance_seconds:
            return utc(now)
        return stamp

    def accept_ticks(self, ticks, now):
        with self.session_factory() as db:
            for tick in ticks:
                if tick.exchange != 1 or tick.price_paise <= 0:
                    continue
                stamp = self.tick_time(tick.timestamp, now)
                instrument = db.scalar(select(Instrument).where(Instrument.token == tick.token))
                if not instrument:
                    continue
                old = db.get(MarketQuote, instrument.symbol)
                if old and utc(stamp) < utc(old.market_at):
                    continue
                if old and utc(stamp) == utc(old.market_at) and tick.volume == old.cumulative_volume and d(Decimal(tick.price_paise) / 100) == old.price:
                    continue
                same_day = old and day_string(old.market_at) == day_string(stamp)
                # Snapshot volume is cumulative. Initial volume is unknown for this candle.
                volume_delta = None
                if same_day and old.cumulative_volume is not None and tick.volume is not None:
                    volume_delta = max(0, tick.volume - old.cumulative_volume)
                price = Decimal(tick.price_paise) / 100
                day_open = Decimal(tick.open_paise or 0) / 100
                if old is None:
                    old = MarketQuote(symbol=instrument.symbol, price=price, day_open=day_open,
                                      market_at=stamp, received_at=now, cumulative_volume=tick.volume)
                    db.add(old)
                else:
                    old.price = price
                    old.day_open = day_open or (old.day_open if same_day else Decimal(0))
                    old.market_at, old.received_at, old.cumulative_volume = stamp, now, tick.volume
                db.flush()
                ingest_tick(db, instrument.symbol, price, stamp, volume_delta, commit=False)
                mark_symbol(db, self.account_id, instrument.symbol, price, commit=False)
                db.commit()

    async def reconcile(self, now):
        # REST snapshots are authoritative; websocket trade packets have no trade ID.
        remote, global_trades, positions = await asyncio.gather(
            self.broker.list_orders(), self.broker.list_trades(), self.broker.positions())
        by_id = {str(row["orderId"]): row for row in remote}
        with self.session_factory() as db:
            orders = db.scalars(select(Order).join(OrderRoute).where(Order.account_id == self.account_id)).all()
            query_orders = [o for o in orders if o.broker_order_id and
                            (o.status in OPEN_STATES or day_string(o.created_at) == day_string(now)
                             or day_string(o.updated_at) == day_string(now))]
            # Completed, unchanged orders do not need another per-order HTTP call.
            query_orders = [o for o in query_orders if o.status in OPEN_STATES or
                            self.trade_versions.get(o.broker_order_id) != self.order_version(by_id.get(o.broker_order_id))]
            ids = [o.broker_order_id for o in query_orders]
        semaphore = asyncio.Semaphore(6)
        async def fetch(order_id):
            async with semaphore:
                return await self.broker.order_trades(order_id)
        batches = await asyncio.gather(*(fetch(order_id) for order_id in ids))
        by_order_trades = dict(zip(ids, batches))
        problems = []
        with self.session_factory() as db:
            account = db.get(Account, self.account_id)
            state = state_for(db, account.id)
            mapped_ids = set()
            seen_trade_ids = set(db.scalars(select(Execution.execution_id).where(Execution.account_id == account.id)).all())
            for order in db.scalars(select(Order).where(Order.account_id == account.id)).all():
                route = db.get(OrderRoute, order.id)
                if not route:
                    problems.append("Existing simulator history must use a separate database")
                    continue
                if order.broker_order_id:
                    mapped_ids.add(order.broker_order_id)
                if order.status in {"SUBMITTING", "UNKNOWN"}:
                    problems.append(f"Order {order.id} has an ambiguous submission")
                trades = by_order_trades.get(order.broker_order_id, [])
                for trade in sorted(trades, key=lambda t: (int(t["tradeTime"]), int(t["tradeId"]))):
                    when = broker_trade_time(trade["tradeTime"], now)
                    signed_qty = int(trade["quantity"])
                    if (int(trade["token"]) != route.token or trade["exchange"] != "NSECM"
                            or trade["product"] != "INTRADAY" or (signed_qty > 0) != (order.side == "BUY")):
                        raise ValueError("Broker trade does not match the attributed order")
                    trade_key = f"021:NSECM:{day_string(when)}:{trade['tradeId']}"
                    seen_trade_ids.add(trade_key)
                    existing = db.scalar(select(Execution).where(
                        Execution.account_id == account.id,
                        Execution.execution_id.like(f"021:NSECM:%:{trade['tradeId']}"),
                    ))
                    if existing:
                        # Repair fills recorded before the live sandbox epoch
                        # discrepancy was observed. Do not apply quantity twice.
                        existing.execution_id = trade_key
                        existing.executed_at = when
                    else:
                        apply_fill(db, order, trade_key, abs(signed_qty), Decimal(trade["price"]) / 100, executed_at=when)
                row = by_id.get(order.broker_order_id)
                if row:
                    expected = abs(int(row["qtyTraded"]))
                    if expected != order.filled_qty:
                        problems.append(f"Order {order.id}: trade list and order filled quantity differ")
                    broker_status = row["status"]
                    if order.filled_qty == order.requested_qty:
                        order.status = "FILLED"
                    elif broker_status in {"Cancelled", "Rejected"}:
                        order.status = "CANCELLED" if broker_status == "Cancelled" else "REJECTED"
                    elif broker_status == "SentForCancellation":
                        order.status = "CANCEL_PENDING"
                    else:
                        order.status = "PARTIALLY_FILLED" if order.filled_qty else "ACKNOWLEDGED"
                    order.reserved_qty = 0 if order.status in {"FILLED", "CANCELLED", "REJECTED"} else order.requested_qty - order.filled_qty
                    order.reason = row.get("reason") or None
                elif order.broker_order_id and order.status in OPEN_STATES:
                    problems.append(f"Order {order.id} absent from today's broker order book")
            for row in remote:
                # This platform owns INTRADAY orders only. A sandbox user may
                # separately hold or trade CNC delivery stock; that activity is
                # isolated by the broker product ledger and cannot change one of
                # our INTRADAY strategy positions.
                if row.get("product") == "INTRADAY" and str(row["orderId"]) not in mapped_ids:
                    problems.append(
                        f"Unattributed INTRADAY broker order {row['orderId']} "
                        f"({row.get('status', 'unknown status')})"
                    )
            # /trades has no order ID, but still proves catch-up coverage.
            for trade in global_trades:
                if trade.get("product") != "INTRADAY":
                    continue
                when = broker_trade_time(trade["tradeTime"], now)
                key = f"021:{trade['exchange']}:{day_string(when)}:{trade['tradeId']}"
                if key not in seen_trade_ids:
                    problems.append(
                        f"INTRADAY broker trade {trade['tradeId']} for token "
                        f"{trade.get('token')} is missing from the strategy ledger"
                    )
            # Charges are derived data: recompute each execution from its
            # immutable price/quantity/side against the active schedule after any
            # broker timestamp normalization, and rebuild position/day totals.
            rebuild_charges(db, account.id)
            local = {}
            for position in db.scalars(select(Position).where(Position.account_id == account.id)).all():
                instrument = db.get(Instrument, position.symbol)
                if instrument:
                    local[instrument.token] = local.get(instrument.token, 0) + position.quantity
            actual = {}
            for row in positions:
                quantity = int(row["netQuantity"])
                if row["product"] != "INTRADAY":
                    continue
                if row["exchange"] != "NSECM":
                    if quantity:
                        problems.append(
                            f"Unsupported {row['exchange']} INTRADAY position for token "
                            f"{row.get('token')} has quantity {quantity}"
                        )
                    continue
                # The live sandbox has returned zero, stale values, and values
                # larger than the executable net for squareOffQuantity. Using
                # it could cross through flat. netQuantity is therefore the
                # authoritative exposure; local strategy quantities must sum
                # to it exactly before entries or close orders are allowed.
                actual[int(row["token"])] = quantity
            if {k:v for k,v in local.items() if v} != {k:v for k,v in actual.items() if v}:
                problems.append("Strategy position sum differs from the broker account; entries blocked")
            state.broker_positions = json.dumps(positions)
            state.last_reconciled_at = now
            state.last_error = "; ".join(dict.fromkeys(problems))[:2000]
            account.recovered = not problems
            db.commit()
        self.remote_orders = remote
        for order_id in ids:
            self.trade_versions[order_id] = self.order_version(by_id.get(order_id))
        self.dirty = False
        self.last_poll = time.monotonic()
        return not problems

    @staticmethod
    def order_version(row):
        return None if row is None else (row.get("qtyTraded"), row.get("lastActivity"), row.get("status"))

    async def submit(self, sub_id, signed_qty, key, now, close_only=False):
        with self.session_factory() as db:
            account = lock_account(db, self.account_id)
            state = state_for(db, account.id)
            sub = db.get(Subscription, sub_id)
            instrument = db.get(Instrument, sub.symbol)
            quote = db.get(MarketQuote, sub.symbol)
            client_id = f"021-{sub.id}-{key}"[:80]
            if db.scalar(select(Order).where(Order.account_id == account.id, Order.client_order_id == client_id)):
                return
            issue = risk_reason(db, account, state, sub, instrument, quote, signed_qty, now, close_only)
            if issue:
                recent = db.scalar(select(RiskEvent.id).where(
                    RiskEvent.subscription_id == sub.id, RiskEvent.code == issue[0],
                    RiskEvent.created_at >= utc(now) - timedelta(seconds=60)))
                if not recent:
                    db.add(RiskEvent(account_id=account.id, subscription_id=sub.id, code=issue[0], message=issue[1]))
                db.commit()
                return
            order = Order(account_id=account.id, subscription_id=sub.id, client_order_id=client_id,
                          symbol=sub.symbol, side="BUY" if signed_qty > 0 else "SELL", requested_qty=abs(signed_qty),
                          reserved_qty=abs(signed_qty), status="SUBMITTING", close_only=close_only, created_at=now)
            db.add(order)
            db.flush()
            route = OrderRoute(order_id=order.id, token=instrument.token,
                               known_order_ids=json.dumps([str(r["orderId"]) for r in self.remote_orders]))
            db.add(route)
            ledger = day_ledger(db, sub.id, now)
            if not close_only:
                ledger.signal_key = key
            if not close_only and sub.strategy_id in {"time_entry", "open_breakout"}:
                ledger.entry_attempted = True
            db.commit()  # Intent and reservation MUST survive a crash before/after POST.
            order_id, token = order.id, route.token
        try:
            broker_id = await self.broker.place_order(token, signed_qty)
        except BrokerError as error:
            with self.session_factory() as db:
                order = db.get(Order, order_id)
                order.status = "UNKNOWN" if error.ambiguous else "REJECTED"
                order.reason = str(error)
                if not error.ambiguous:
                    order.reserved_qty = 0
                db.get(Account, self.account_id).recovered = False
                db.commit()
        else:
            with self.session_factory() as db:
                order = db.get(Order, order_id)
                order.broker_order_id, order.status = broker_id, "ACKNOWLEDGED"
                db.get(OrderRoute, order_id).broker_reference = f"{self.account_id}:{broker_id}"
                db.get(Account, self.account_id).recovered = False
                record_simulation_event(db, self.account_id, "ORDER_ACK", f"021 accepted {order.side} {order.requested_qty} {order.symbol}", f"Broker order {broker_id}; awaiting confirmed trades")
                db.commit()
        self.dirty = True

    async def cancel(self, order_id, now):
        with self.session_factory() as db:
            order = db.get(Order, order_id)
            route = db.get(OrderRoute, order_id)
            if not order.broker_order_id or not route or order.status not in OPEN_STATES:
                return
            if route.last_cancel_at and (utc(now) - utc(route.last_cancel_at)).total_seconds() < 2:
                return
            broker_id, token = order.broker_order_id, route.token
            route.last_cancel_at = now
            db.commit()
        try:
            await self.broker.cancel_order(broker_id, token)
        except BrokerError as error:
            with self.session_factory() as db:
                db.get(Order, order_id).reason = f"Cancel outcome pending reconciliation: {error}"
                db.commit()
        else:
            with self.session_factory() as db:
                db.get(Order, order_id).status = "CANCEL_PENDING"
                db.commit()
        self.dirty = True

    async def advance(self, now):
        """Cancellations and exits precede all new entries, even when paused."""
        exits, entries, cancellations = [], [], []
        with self.session_factory() as db:
            account = db.get(Account, self.account_id)
            state = state_for(db, account.id)
            account.worker_heartbeat = now
            killing = account.kill_state not in {"RUNNING", "HALTED"}
            for sub in db.scalars(select(Subscription).where(Subscription.account_id == account.id)).all():
                ledger = day_ledger(db, sub.id, now)
                if daily_net(db, sub, now) <= -sub.max_daily_loss:
                    if not ledger.loss_latched:
                        db.add(RiskEvent(account_id=account.id, subscription_id=sub.id, code="DAILY_LOSS", message="Daily loss breached; cancel entries and close this strategy"))
                    ledger.loss_latched = True
                instrument = db.get(Instrument, sub.symbol)
                quote = db.get(MarketQuote, sub.symbol)
                if not instrument:
                    continue
                if utc(now).astimezone(IST).time() >= settings.market_close:
                    ledger.exiting = True
                position = sub_position(db, sub)
                latest_fill = db.scalar(select(Execution).join(Order).where(
                    Order.subscription_id == sub.id).order_by(Execution.executed_at.desc()).limit(1))
                if position and position.quantity and latest_fill and day_string(latest_fill.executed_at) < day_string(now):
                    ledger.exiting = True
                decision = None
                # Emergency exits never invoke strategy code.
                if not (killing or ledger.loss_latched or ledger.exiting):
                    try:
                        decision = strategy_decision(db, sub, quote, instrument, now)
                    except Exception as error:
                        sub.status = "PAUSED"
                        ledger.exiting = True
                        db.add(RiskEvent(account_id=account.id, subscription_id=sub.id,
                                         code="STRATEGY_FAILURE", message=f"Strategy paused and exiting: {type(error).__name__}"))
                should_exit = killing or ledger.loss_latched or ledger.exiting or (decision and decision.close_only)
                pending = open_orders(db, sub.id)
                if should_exit:
                    if decision and decision.close_only and sub.strategy_id == "ma_cross":
                        ledger.signal_key = decision.key
                    cancellations.extend(o.id for o in pending if not o.close_only or (
                        killing and state.kill_started_at and utc(o.created_at) < utc(state.kill_started_at)))
                    position = sub_position(db, sub)
                    # Never close until entry cancellation and all fills are reconciled.
                    if position and position.quantity and not pending and account.recovered:
                        close_qty = min(abs(position.quantity), instrument.freeze_quantity)
                        close_qty = close_qty // instrument.lot_size * instrument.lot_size
                        exits.append((sub.id, -close_qty if position.quantity > 0 else close_qty, f"close-{uuid.uuid4().hex[:16]}"))
                elif decision and not decision.close_only and state.enabled and account.kill_state == "RUNNING" and account.recovered:
                    entries.append((sub.id, decision.signed_qty, decision.key))
            if killing:
                unresolved = db.scalar(select(Order.id).where(Order.account_id == account.id, Order.status.in_(OPEN_STATES)))
                remaining = db.scalar(select(Position.id).where(Position.account_id == account.id, Position.quantity != 0))
                elapsed = int((utc(now) - utc(state.kill_started_at or now)).total_seconds() * 1000)
                if not remaining and not unresolved and account.recovered:
                    account.kill_state = "HALTED"
                    state.kill_elapsed_ms = elapsed
                    state.enabled = False
                elif elapsed >= 10000:
                    account.kill_state = "NEEDS_ATTENTION"
                    state.last_error = "Kill exceeded 10 seconds; continuing reconciliation/cancel/close. " + state.last_error[:1500]
                else:
                    account.kill_state = "CANCELLING" if unresolved else "CLOSING"
            db.commit()
        await asyncio.gather(*(self.cancel(order_id, now) for order_id in cancellations))
        # Every submission re-reads state after prior network awaits.
        for sub_id, quantity, key in exits:
            await self.submit(sub_id, quantity, key, now, close_only=True)
        for sub_id, quantity, key in entries:
            await self.submit(sub_id, quantity, key, now)

    def fail_closed(self, message):
        with self.session_factory() as db:
            account = db.get(Account, self.account_id)
            if account:
                account.recovered = False
                account.worker_heartbeat = datetime.now(timezone.utc)
                state_for(db, account.id).last_error = message[:2000]
                db.commit()
