import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
import random
import time
from sqlalchemy import select, text
from websockets.asyncio.client import connect
from .brokers.broker_021 import Broker021Adapter, BrokerError
from .brokers.feed_021 import decode_market, decode_order_event
from .config import settings
from .database import Base, SessionLocal, engine
from .engine_021 import TradingEngine, state_for
from .models import Account, BrokerState, Instrument, Subscription

log = logging.getLogger(__name__)


@contextmanager
def worker_lock():
    """One execution writer across processes; released by the OS on crash."""
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            if not connection.scalar(text("SELECT pg_try_advisory_lock(210306)")):
                raise RuntimeError("Another trading worker already owns this database")
            connection.commit()
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(210306)"))
    elif engine.dialect.name == "sqlite":
        path = str(engine.url.database) + ".worker.lock"
        with open(path, "a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
    else:
        raise RuntimeError("Trading worker requires PostgreSQL or SQLite")


def subscriptions():
    with SessionLocal() as db:
        return sorted(set(db.scalars(select(Instrument.token).join(
            Subscription, Subscription.symbol == Instrument.symbol
        ).where(Subscription.account_id == settings.broker_021_account_id)).all()))


async def socket_loop(broker, channel, queue):
    delay = 1
    while True:
        try:
            if not settings.broker_021_access_token:
                await asyncio.sleep(2)
                continue
            with SessionLocal() as db:
                if not db.get(Account, settings.broker_021_account_id):
                    await asyncio.sleep(1)
                    continue
            url = await broker.websocket_url(channel)
            async with connect(url, open_timeout=5, ping_interval=20, ping_timeout=20, max_size=1048576) as socket:
                await queue.put(("connection", (channel, True)))
                delay = 1
                selected = []
                while True:
                    if channel == "market":
                        desired = subscriptions()
                        if desired != selected:
                            removed = sorted(set(selected) - set(desired))
                            added = sorted(set(desired) - set(selected))
                            # The server counts current + requested even on unsubscribe.
                            if removed:
                                # Reconnect rather than exceed the documented 100-token limit.
                                break
                            if added:
                                await socket.send(json.dumps({"Task": "subscribe", "Mode": "full",
                                                              "Instruments": [[1, token] for token in added]}))
                            selected = desired
                    try:
                        frame = await asyncio.wait_for(socket.recv(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    if not isinstance(frame, bytes):
                        continue
                    now = datetime.now(timezone.utc)
                    if channel == "market":
                        ticks = decode_market(frame, now)
                        if ticks:
                            await queue.put(("ticks", (ticks, now)))
                    elif decode_order_event(frame) is not None:
                        await queue.put(("orders", None))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Never log URLs (they contain ephemeral credentials).
            log.warning("021 %s socket reconnecting (%s)", channel, type(error).__name__)
        finally:
            try:
                queue.put_nowait(("connection", (channel, False)))
            except asyncio.QueueFull:
                pass
        await asyncio.sleep(delay + random.uniform(0, .25))
        delay = min(30, delay * 2)


async def run():
    Base.metadata.create_all(engine)
    broker = Broker021Adapter(settings.broker_021_access_token)
    trader = TradingEngine(broker, settings.broker_021_account_id)
    queue = asyncio.Queue(maxsize=10000)
    initialized = False
    tasks = []
    last_master = 0
    master_task = None
    with worker_lock():
        try:
            while True:
                now = datetime.now(timezone.utc)
                if not initialized:
                    initialized = trader.startup()
                    if not initialized:
                        await asyncio.sleep(1)
                        continue
                    tasks = [asyncio.create_task(socket_loop(broker, name, queue)) for name in ("market", "orders")]
                if not settings.broker_021_access_token:
                    trader.fail_closed("021 access token missing. Fill .env and run the login command, then restart services.")
                    await asyncio.sleep(2)
                    continue
                try:
                    # A cached daily master is not downloaded during emergency handling.
                    with SessionLocal() as db:
                        account = db.get(Account, settings.broker_021_account_id)
                        killing = account.kill_state not in {"RUNNING", "HALTED"}
                    if time.monotonic() - last_master > 60 and not killing and master_task is None:
                        master_task = asyncio.create_task(trader.refresh_instruments(now))
                        last_master = time.monotonic()
                    if master_task is not None and master_task.done():
                        finished, master_task = master_task, None
                        finished.result()
                    for _ in range(min(queue.qsize(), 500)):
                        kind, value = queue.get_nowait()
                        if kind == "ticks":
                            trader.accept_ticks(*value)
                        elif kind == "orders":
                            trader.dirty = True
                        else:
                            channel, connected = value
                            with SessionLocal() as db:
                                state = state_for(db, settings.broker_021_account_id)
                                setattr(state, f"{channel}_connected", connected)
                                if not connected:
                                    db.get(Account, settings.broker_021_account_id).recovered = False
                                db.commit()
                            trader.dirty = True
                    interval = .25 if killing else (.5 if trader.dirty else 2)
                    if time.monotonic() >= trader.next_poll and time.monotonic() - trader.last_poll >= interval:
                        # Three account snapshots are fetched concurrently, but a
                        # sandbox response can legitimately take several seconds.
                        async with asyncio.timeout(12):
                            await trader.reconcile(datetime.now(timezone.utc))
                        trader.failures = 0
                    await trader.advance(datetime.now(timezone.utc))
                except (BrokerError, ValueError, KeyError, TypeError, TimeoutError) as error:
                    trader.fail_closed(str(error) or "Broker reconciliation timed out; entries blocked")
                    trader.failures += 1
                    trader.next_poll = time.monotonic() + (1 if killing else min(15, 2 ** min(trader.failures, 4)))
                    # Continue cancellation attempts even when position polling failed;
                    # recovered=False prevents any new or unconfirmed closing orders.
                    await trader.advance(datetime.now(timezone.utc))
                except Exception as error:
                    trader.fail_closed(f"Worker error: {type(error).__name__}; entries blocked")
                    log.exception("021 supervisor failure")
                    await asyncio.sleep(1)
                await asyncio.sleep(.1)
        finally:
            for task in tasks:
                task.cancel()
            if master_task:
                master_task.cancel()
                tasks.append(master_task)
            await asyncio.gather(*tasks, return_exceptions=True)
            await broker.close()
