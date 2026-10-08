"""Execution worker: simulator loop or Alpaca Paper reconciliation and data feed."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from .config import settings
from .database import Base, SessionLocal, engine
from .models import Account, Order, SimulatorSession, Subscription
from .services import (
    d, evaluate_strategy, ingest_tick, mark_symbol, reconcile_alpaca_orders,
    record_simulation_event, submit_order,
)


def active_alpaca_symbols() -> set[str]:
    with SessionLocal() as db:
        return set(db.scalars(select(Subscription.symbol).join(
            SimulatorSession, SimulatorSession.account_id == Subscription.account_id
        ).join(Account, Account.id == Subscription.account_id).where(
            SimulatorSession.active.is_(True), Account.kill_state == "RUNNING",
            Subscription.status == "RUNNING",
        )).all())


def _suffix(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def process_alpaca_trade(symbol: str, price: Decimal, timestamp: datetime, size: int | None):
    """Persist one broker trade, then evaluate only subscriptions opted into the feed."""
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
            if not subscriptions:
                continue
            mark_symbol(db, account.id, symbol, price)
            for subscription in subscriptions:
                signal = evaluate_strategy(db, subscription)
                if signal:
                    side, quantity, _ = signal
                    record_simulation_event(db, account.id, "SIGNAL", f"{subscription.strategy_id} emitted {side}", "Alpaca market-data closed-candle signal")
                    submit_order(db, account, subscription, side, quantity, price, client_id=f"alpaca-{account.id}-{subscription.id}-{_suffix(timestamp)}")
        db.commit()


async def supervise():
    # Local/direct starts do not go through Docker's migration service.
    Base.metadata.create_all(engine)
    while True:
        with SessionLocal() as db:
            for account in db.scalars(select(Account)).all():
                account.worker_heartbeat = datetime.now(timezone.utc)
                unresolved = db.scalars(select(Order).where(Order.account_id == account.id, Order.status == "UNKNOWN")).first()
                account.recovered = unresolved is None and account.kill_state in {"RUNNING", "HALTED"}
                if settings.is_alpaca_paper:
                    reconcile_alpaca_orders(db, account)
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


async def alpaca_market_data_loop():
    """One backend WebSocket receives IEX trades and fans them into account ledgers."""
    if not settings.is_alpaca_paper:
        return
    if not settings.alpaca_configured:
        raise RuntimeError("ALPACA_PAPER requires ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY")
    import websockets

    while True:
        symbols = active_alpaca_symbols()
        if not symbols:
            await asyncio.sleep(2)
            continue
        try:
            url = f"wss://stream.data.alpaca.markets/v2/{settings.alpaca_data_feed}"
            async with websockets.connect(url, ping_interval=20, close_timeout=5) as socket:
                await socket.send(json.dumps({"action": "auth", "key": settings.alpaca_api_key_id, "secret": settings.alpaca_api_secret_key}))
                await socket.send(json.dumps({"action": "subscribe", "trades": sorted(symbols)}))
                reconnect_at = asyncio.get_running_loop().time() + 30
                while asyncio.get_running_loop().time() < reconnect_at:
                    try:
                        message = await asyncio.wait_for(socket.recv(), timeout=2)
                    except asyncio.TimeoutError:
                        continue
                    for event in json.loads(message):
                        if event.get("T") != "t" or event.get("S") not in symbols:
                            continue
                        timestamp = datetime.fromisoformat(event["t"].replace("Z", "+00:00"))
                        process_alpaca_trade(event["S"], Decimal(str(event["p"])), timestamp, int(event.get("s") or 0) or None)
        except Exception:
            # The next connection attempt recovers from temporary feed/network failures.
            await asyncio.sleep(5)


async def main():
    if settings.is_alpaca_paper:
        await asyncio.gather(supervise(), alpaca_market_data_loop())
    else:
        await supervise()


if __name__ == "__main__":
    asyncio.run(main())
