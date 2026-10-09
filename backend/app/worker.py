"""Execution worker: deterministic simulator or Upstox live-data + sandbox orders."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from .config import settings
from .database import Base, SessionLocal, engine
from .models import Account, Order, SimulatorSession, Subscription
from .services import d, evaluate_strategy, ingest_tick, mark_symbol, record_simulation_event, submit_order


def upstox_feed_active() -> bool:
    with SessionLocal() as db:
        return bool(db.scalars(select(Subscription.id).join(
            SimulatorSession, SimulatorSession.account_id == Subscription.account_id
        ).join(Account, Account.id == Subscription.account_id).where(
            SimulatorSession.active.is_(True), Account.kill_state == "RUNNING",
            Subscription.status == "RUNNING", Subscription.symbol == settings.upstox_default_symbol,
        )).first())


def _suffix(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def process_upstox_tick(price: Decimal, timestamp: datetime, size: int | None):
    """Persist one live Upstox tick, then evaluate opted-in strategies."""
    symbol = settings.upstox_default_symbol
    with SessionLocal() as db:
        accounts = db.scalars(select(Account).join(
            SimulatorSession, SimulatorSession.account_id == Account.id
        ).where(SimulatorSession.active.is_(True), Account.kill_state == "RUNNING", Account.recovered.is_(True))).all()
        if not accounts:
            return
        ingest_tick(db, symbol, price, timestamp, size)
        for account in accounts:
            subscriptions = db.scalars(select(Subscription).where(
                Subscription.account_id == account.id, Subscription.status == "RUNNING", Subscription.symbol == symbol,
            )).all()
            mark_symbol(db, account.id, symbol, price)
            for subscription in subscriptions:
                signal = evaluate_strategy(db, subscription)
                if signal:
                    side, quantity, _ = signal
                    record_simulation_event(db, account.id, "SIGNAL", f"{subscription.strategy_id} emitted {side}", "Upstox live-data closed-candle signal")
                    submit_order(db, account, subscription, side, quantity, price, client_id=f"upstox-{account.id}-{subscription.id}-{_suffix(timestamp)}")
        db.commit()


def _find_ltpc(value):
    """Extract a last-trade payload from the SDK's decoded V3 feed dictionary."""
    if isinstance(value, dict):
        ltpc = value.get("ltpc")
        if isinstance(ltpc, dict) and ltpc.get("ltp") is not None:
            return ltpc
        for child in value.values():
            found = _find_ltpc(child)
            if found:
                return found
    return None


def run_upstox_market_stream():
    """Blocking official SDK loop, isolated in a worker thread by asyncio."""
    import upstox_client

    while True:
        if not upstox_feed_active():
            time.sleep(2)
            continue
        configuration = upstox_client.Configuration()
        configuration.access_token = settings.upstox_analytics_token
        streamer = upstox_client.MarketDataStreamerV3(
            upstox_client.ApiClient(configuration), [settings.upstox_default_instrument_key], "full"
        )

        def on_message(message):
            ltpc = _find_ltpc(message)
            if not ltpc:
                return
            try:
                price = Decimal(str(ltpc["ltp"]))
                raw_time = ltpc.get("ltt")
                timestamp = datetime.fromtimestamp(int(raw_time) / 1000, tz=timezone.utc) if raw_time else datetime.now(timezone.utc)
                process_upstox_tick(price, timestamp, int(ltpc.get("ltq") or 0) or None)
            except (KeyError, TypeError, ValueError, ArithmeticError):
                return

        streamer.on("message", on_message)
        streamer.auto_reconnect(True, 5, 0)
        try:
            streamer.connect()
        except Exception:
            time.sleep(5)


async def supervise():
    Base.metadata.create_all(engine)
    while True:
        with SessionLocal() as db:
            for account in db.scalars(select(Account)).all():
                account.worker_heartbeat = datetime.now(timezone.utc)
                unresolved = db.scalars(select(Order).where(Order.account_id == account.id, Order.status == "UNKNOWN")).first()
                account.recovered = unresolved is None and account.kill_state in {"RUNNING", "HALTED"}
                if settings.is_upstox_sandbox:
                    continue
                session = db.get(SimulatorSession, account.id)
                if session and session.active and account.kill_state == "RUNNING" and account.recovered:
                    session.tick_index += 1
                    session.virtual_time = (session.virtual_time or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
                    wave = [0, -4, -8, -3, 5, 11, 16, 8, 1, -6, 4, 14][session.tick_index % 12]
                    session.last_price = d(Decimal("2500") + wave)
                    ingest_tick(db, "RELIANCE", session.last_price, session.virtual_time, 100)
                    record_simulation_event(db, account.id, "TICK", f"RELIANCE tick ₹{session.last_price}", f"Virtual minute {session.virtual_time.isoformat()}")
                    session.virtual_time = session.virtual_time.replace(tzinfo=timezone.utc) + timedelta(minutes=1)
                    for sub in db.scalars(select(Subscription).where(Subscription.account_id == account.id, Subscription.status == "RUNNING")).all():
                        signal = evaluate_strategy(db, sub)
                        if signal:
                            side, quantity, _ = signal
                            record_simulation_event(db, account.id, "SIGNAL", f"{sub.strategy_id} emitted {side}", "Virtual market stream closed-candle signal")
                            submit_order(db, account, sub, side, quantity, session.last_price, client_id=f"stream-{account.id}-{sub.id}-{session.tick_index}")
            db.commit()
        await asyncio.sleep(2)


async def main():
    if settings.is_upstox_sandbox:
        if not settings.upstox_configured:
            raise RuntimeError("UPSTOX_SANDBOX requires UPSTOX_ANALYTICS_TOKEN and UPSTOX_SANDBOX_ACCESS_TOKEN")
        await asyncio.gather(supervise(), asyncio.to_thread(run_upstox_market_stream))
    else:
        await supervise()


if __name__ == "__main__":
    asyncio.run(main())
