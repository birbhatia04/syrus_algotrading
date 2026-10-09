"""Authenticated operator controls. Only the worker submits strategy orders."""
from datetime import datetime, timezone
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .brokers.broker_021 import Broker021Adapter, BrokerError
from .charges import schedule
from .config import settings
from .database import get_db
from .engine_021 import state_for, lock_account
from .models import Account, AuditEvent, BrokerState, Instrument, Order, OrderRoute, Position, Subscription, User
from .security import current_user, user_account
from .trading_rules import OPEN_STATES, utc

router = APIRouter(prefix="/api/v1/broker")


def bound_account(user, db):
    account = user_account(user, db)
    if not settings.is_021:
        raise HTTPException(409, "021 controls require ENVIRONMENT=021_SANDBOX")
    if account.id != settings.broker_021_account_id:
        raise HTTPException(403, "This user is not bound to the configured sandbox. Set BROKER_021_ACCOUNT_ID explicitly.")
    return account


@router.get("/status")
def status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    state = db.get(BrokerState, account.id)
    heartbeat = utc(account.worker_heartbeat)
    fresh = (datetime.now(timezone.utc) - heartbeat).total_seconds() < 5
    return {
        "environment": settings.environment, "configured": bool(settings.broker_021_access_token),
        "bound": account.id == settings.broker_021_account_id,
        "enabled": bool(state and state.enabled), "worker_alive": fresh,
        "market_connected": bool(state and state.market_connected and fresh),
        "orders_connected": bool(state and state.orders_connected and fresh),
        "reconciled": bool(account.recovered and fresh),
        "last_error": state.last_error if state else "Start the execution worker",
        "kill_elapsed_ms": state.kill_elapsed_ms if state else None,
        "charge_schedule": schedule(), "charges_assumed": False,
    }


@router.post("/stream/{action}")
def stream(action: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = bound_account(user, db)
    account = lock_account(db, account.id)
    if action not in {"start", "stop"}:
        raise HTTPException(404, "Unknown feed action")
    if action == "start" and not settings.broker_021_access_token:
        raise HTTPException(409, "Fill .env and run python -m app.brokers.login_021, then restart API and worker")
    if action == "start" and account.kill_state != "RUNNING":
        raise HTTPException(409, "Resolve the kill switch before starting")
    state = state_for(db, account.id)
    state.enabled = action == "start"
    db.commit()
    return {"active": state.enabled}


@router.get("/instruments")
def instruments(q: str = Query("", max_length=20), user: User = Depends(current_user), db: Session = Depends(get_db)):
    bound_account(user, db)
    rows = db.scalars(select(Instrument).where(Instrument.symbol.startswith(q.upper())).order_by(Instrument.symbol).limit(50)).all()
    return [{"symbol": i.symbol, "token": i.token, "exchange": "NSE", "lot_size": i.lot_size,
             "ticksize_paise": i.ticksize, "trading_day": i.trading_day} for i in rows]


class Resolution(BaseModel):
    broker_order_id: str = Field(pattern=r"^[0-9]+$")


@router.post("/orders/{order_id}/resolve")
async def resolve(order_id: int, body: Resolution, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = bound_account(user, db)
    order = db.scalar(select(Order).where(Order.id == order_id, Order.account_id == account.id))
    route = db.get(OrderRoute, order_id)
    if not order or not route or order.status != "UNKNOWN" or order.broker_order_id:
        raise HTTPException(409, "Only an unresolved submission without a broker ID can be linked")
    if body.broker_order_id in json.loads(route.known_order_ids):
        raise HTTPException(409, "This broker order existed before the submission")
    if db.scalar(select(Order.id).where(Order.account_id == account.id, Order.broker_order_id == body.broker_order_id)):
        raise HTTPException(409, "Broker order already belongs to a strategy")
    broker = Broker021Adapter(settings.broker_021_access_token)
    try:
        rows = await broker._list(f"/orders/{body.broker_order_id}")
    except BrokerError as error:
        raise HTTPException(409, str(error)) from error
    finally:
        await broker.close()
    if len(rows) != 1:
        raise HTTPException(409, "Broker order not found")
    row = rows[0]
    signed = int(row["qtyTraded"]) + int(row["qtyRemaining"])
    placed = datetime.fromtimestamp(int(row["time"]), timezone.utc)
    if (int(row["token"]) != route.token or row["exchange"] != "NSECM" or row["product"] != "INTRADAY"
            or abs(signed) != order.requested_qty or (signed > 0) != (order.side == "BUY")
            or abs((placed - utc(order.created_at)).total_seconds()) > 120):
        raise HTTPException(409, "Broker order does not match the durable submission")
    order.broker_order_id, order.status = body.broker_order_id, "ACKNOWLEDGED"
    route.broker_reference = f"{account.id}:{body.broker_order_id}"
    order.reason = "Operator linked an ambiguous submission; REST reconciliation pending"
    account.recovered = False
    db.add(AuditEvent(account_id=account.id, actor_user_id=user.id, action="RESOLVE_SUBMISSION",
                      detail=f"local={order.id};broker={body.broker_order_id}"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Broker order was already linked by another request") from None
    return {"status": "reconciliation_pending"}


def request_kill(db, account, user_id):
    account = lock_account(db, account.id)
    state = state_for(db, account.id)
    if account.kill_state == "HALTED":
        return
    if account.kill_state == "RUNNING":
        state.kill_started_at = datetime.now(timezone.utc)
        state.kill_elapsed_ms = None
    account.kill_state = "KILL_REQUESTED"
    state.enabled = False
    for sub in db.scalars(select(Subscription).where(Subscription.account_id == account.id)).all():
        sub.status = "PAUSED"
    db.add(AuditEvent(account_id=account.id, actor_user_id=user_id, action="KILL_REQUESTED",
                      detail="Worker must cancel, reconcile confirmed fills, and close attributed positions"))
    db.commit()
