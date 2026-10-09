"""Deterministic simulator execution worker."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from .database import Base, SessionLocal, engine
from .config import settings
from .models import Account, Order, SimulatorSession, Subscription
from .services import d, evaluate_strategy, ingest_tick, mark_symbol, record_simulation_event, submit_order


async def supervise():
    Base.metadata.create_all(engine)
    while True:
        with SessionLocal() as db:
            for account in db.scalars(select(Account)).all():
                account.worker_heartbeat = datetime.now(timezone.utc)
                unresolved = db.scalars(select(Order).where(Order.account_id == account.id, Order.status == "UNKNOWN")).first()
                account.recovered = unresolved is None and account.kill_state in {"RUNNING", "HALTED"}
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
    if settings.is_021:
        from .runtime_021 import run
        await run()
    else:
        await supervise()


if __name__ == "__main__":
    asyncio.run(main())
