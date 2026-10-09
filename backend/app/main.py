from __future__ import annotations
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .models import Account, AuditEvent, Candle, Execution, Order, Position, RiskEvent, Session as AuthSession, SimulationEvent, SimulatorSession, Strategy, Subscription, User, UserProfile
from .security import create_session, current_user, hash_password, user_account, verify_password
from .services import aggregate_account, d, evaluate_strategy, ingest_tick, perform_kill, position_values, record_simulation_event, reject_risk, seed_strategies, submit_order
from .models import BrokerState, Instrument, MarketQuote
from .broker_api import router as broker_router, bound_account, request_kill
from .engine_021 import lock_account
from .trading_rules import SUPPORTED, OPEN_STATES, daily_net, sub_position, utc, day_string

@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_strategies(db)
        # Recovery gate: durable state is loaded and pending exposure remains reserved.
        for account in db.scalars(select(Account)).all():
            if not settings.is_021:
                account.recovered = account.kill_state in {"RUNNING", "HALTED"}
        db.commit()
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.include_router(broker_router)
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class SubscribeBody(BaseModel):
    strategy_id: str
    symbol: Optional[str] = Field(default=None, pattern=r"^[A-Z][A-Z0-9.-]{1,19}$")


class RiskBody(BaseModel):
    max_daily_loss: Decimal = Field(gt=0, le=1000000)
    max_position_size: int = Field(gt=0, le=100000)
    max_orders_per_minute: int = Field(gt=0, le=1000)


class ParametersBody(BaseModel):
    quantity: int = Field(gt=0, le=100000)
    side: str = Field(default="BUY", pattern="^(BUY|SELL)$")


class SimOrderBody(BaseModel):
    subscription_id: int
    side: str
    quantity: int = Field(gt=0)
    price: Decimal = Field(gt=0)
    scenario: str = Field(default="full", pattern="^(full|partial|reject|pending|cancel_race)$")
    client_order_id: Optional[str] = None


class DemoBody(BaseModel):
    scenario: str = Field(default="vertical", pattern="^(vertical|full|partial|reject|pending|stale_feed|cancel_race)$")


class ProfileBody(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=30, pattern=r"^[0-9+() -]+$")
    city: str = Field(min_length=2, max_length=100)
    trading_experience: str = Field(pattern=r"^(BEGINNER|INTERMEDIATE|ADVANCED)$")
    risk_profile: str = Field(pattern=r"^(CONSERVATIVE|BALANCED|AGGRESSIVE)$")


def iso(value):
    if not value:
        return None
    # SQLite returns DATETIME values without tzinfo. They are stored as UTC, so
    # restore that fact before serializing for JavaScript's date parser.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def num(value):
    return float(value or 0)


def sub_json(db, sub):
    strategy = db.get(Strategy, sub.strategy_id)
    position = sub_position(db, sub)
    net = position_values(position)[1] if position else d(0)
    today = daily_net(db, sub, datetime.now(timezone.utc))
    return {"id": sub.id, "strategy_id": sub.strategy_id, "name": strategy.name, "timeframe": strategy.timeframe, "symbol": sub.symbol, "status": sub.status, "parameters": json.loads(sub.parameters), "max_daily_loss": num(sub.max_daily_loss), "max_position_size": sub.max_position_size, "max_orders_per_minute": sub.max_orders_per_minute, "quantity": position.quantity if position else 0, "net_pnl": num(net), "daily_net_pnl": num(today)}


def order_json(order):
    return {"id": order.id, "subscription_id": order.subscription_id, "client_order_id": order.client_order_id, "broker_order_id": order.broker_order_id, "symbol": order.symbol, "side": order.side, "requested_qty": order.requested_qty, "filled_qty": order.filled_qty, "remaining_qty": order.requested_qty - order.filled_qty, "average_fill_price": num(order.average_fill_price), "status": order.status, "reason": order.reason, "close_only": order.close_only, "created_at": iso(order.created_at)}


@app.get("/api/v1/health/live")
def live():
    return {"status": "live"}


@app.get("/api/v1/health/ready")
def ready(db: Session = Depends(get_db)):
    db.scalar(select(func.count(User.id)))
    return {"status": "ready", "broker": settings.environment}


@app.post("/api/v1/auth/register", status_code=201)
def register(body: Credentials, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user); db.flush()
    account = Account(user_id=user.id, name="021 sandbox" if settings.is_021 else "Primary simulator", recovered=not settings.is_021)
    db.add(account); db.commit()
    return {"token": create_session(db, user.id), "user": {"id": user.id, "email": user.email, "role": user.role}, "account_id": account.id}


@app.post("/api/v1/auth/login")
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    account = user_account(user, db)
    return {"token": create_session(db, user.id), "user": {"id": user.id, "email": user.email, "role": user.role}, "account_id": account.id}


@app.post("/api/v1/auth/logout", status_code=204)
def logout(user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.query(AuthSession).filter(AuthSession.user_id == user.id).delete(); db.commit()


@app.get("/api/v1/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    return {"id": user.id, "email": user.email, "role": user.role, "profile_complete": profile is not None, "profile": profile_json(profile) if profile else None, "account": {"id": account.id, "name": account.name, "status": account.status, "kill_state": account.kill_state}}


def profile_json(profile: UserProfile):
    return {"full_name": profile.full_name, "phone": profile.phone, "city": profile.city, "trading_experience": profile.trading_experience, "risk_profile": profile.risk_profile}


@app.put("/api/v1/profile")
def save_profile(body: ProfileBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    if not profile:
        profile = UserProfile(user_id=user.id, **body.model_dump())
        db.add(profile)
    else:
        for field, value in body.model_dump().items():
            setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile_json(profile)


@app.get("/api/v1/strategies")
def strategies(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": s.id, "name": s.name, "timeframe": s.timeframe, "description": s.description, "rules": s.rules, "default_parameters": json.loads(s.default_parameters)} for s in db.scalars(select(Strategy)).all() if not settings.is_021 or s.id in SUPPORTED]


@app.get("/api/v1/subscriptions")
def subscriptions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    return [sub_json(db, s) for s in db.scalars(select(Subscription).where(Subscription.account_id == account.id)).all()]


@app.post("/api/v1/subscriptions", status_code=201)
def subscribe(body: SubscribeBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    strategy = db.get(Strategy, body.strategy_id)
    if not strategy: raise HTTPException(404, "Strategy not found")
    symbol = body.symbol or "RELIANCE"
    if settings.is_021:
        bound_account(user, db)
        if body.strategy_id not in SUPPORTED:
            raise HTTPException(409, "Choose one of the three 021 strategies")
        instrument = db.get(Instrument, symbol)
        if not instrument or instrument.trading_day != day_string(datetime.now(timezone.utc)):
            raise HTTPException(409, "Symbol unavailable in today's NSE instrument master; start the worker first")
    existing = db.scalar(select(Subscription).where(Subscription.account_id == account.id, Subscription.strategy_id == body.strategy_id))
    if existing:
        if existing.symbol != symbol:
            has_position = db.scalar(select(func.count(Position.id)).where(Position.subscription_id == existing.id, Position.quantity != 0)) or 0
            has_open_order = db.scalar(select(func.count(Order.id)).where(Order.subscription_id == existing.id, Order.status.not_in(["FILLED", "CANCELLED", "REJECTED", "RISK_REJECTED"]))) or 0
            if existing.status == "RUNNING" or has_position or has_open_order:
                raise HTTPException(409, "Pause the strategy, flatten its position, and resolve open orders before changing its symbol")
            existing.symbol = symbol
            db.commit()
        return sub_json(db, existing)
    sub = Subscription(account_id=account.id, strategy_id=strategy.id, symbol=symbol, parameters=strategy.default_parameters)
    db.add(sub); db.commit(); db.refresh(sub)
    return sub_json(db, sub)


def owned_sub(db, account_id, sub_id):
    sub = db.scalar(select(Subscription).where(Subscription.id == sub_id, Subscription.account_id == account_id))
    if not sub: raise HTTPException(404, "Subscription not found")
    return sub


@app.post("/api/v1/subscriptions/{sub_id}/{action}")
def control_subscription(sub_id: int, action: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    if settings.is_021:
        account = lock_account(db, account.id)
    sub = owned_sub(db, account.id, sub_id)
    if action not in {"start", "pause"}: raise HTTPException(404, "Unknown action")
    if settings.is_021:
        bound_account(user, db)
        if sub.strategy_id not in SUPPORTED:
            raise HTTPException(409, "Legacy simulator strategy cannot run against 021")
        if action == "start" and (not account.recovered or (datetime.now(timezone.utc) - utc(account.worker_heartbeat)).total_seconds() > 5):
            raise HTTPException(409, "Wait for the worker to reconcile the 021 account")
    if action == "start" and account.kill_state != "RUNNING": raise HTTPException(409, "Reset kill switch before starting")
    sub.status = "RUNNING" if action == "start" else "PAUSED"; db.commit()
    return sub_json(db, sub)


@app.patch("/api/v1/subscriptions/{sub_id}/risk-limits")
def update_risk(sub_id: int, body: RiskBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    if settings.is_021:
        account = lock_account(db, account.id)
    sub = owned_sub(db, account.id, sub_id)
    sub.max_daily_loss, sub.max_position_size, sub.max_orders_per_minute = body.max_daily_loss, body.max_position_size, body.max_orders_per_minute
    db.add(AuditEvent(account_id=account.id, actor_user_id=user.id, action="RISK_LIMITS_UPDATED", detail=f"subscription={sub.id}")); db.commit()
    return sub_json(db, sub)


@app.patch("/api/v1/subscriptions/{sub_id}/parameters")
def parameters(sub_id: int, body: ParametersBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    sub = owned_sub(db, account.id, sub_id)
    position = sub_position(db, sub)
    pending = db.scalar(select(Order.id).where(Order.subscription_id == sub.id, Order.status.in_(OPEN_STATES)))
    if sub.status == "RUNNING" or (position and position.quantity) or pending:
        raise HTTPException(409, "Pause and flatten the strategy before changing its parameters")
    if settings.is_021:
        bound_account(user, db)
        instrument = db.get(Instrument, sub.symbol)
        if not instrument or body.quantity % instrument.lot_size or body.quantity > instrument.freeze_quantity:
            raise HTTPException(422, "Quantity violates the instrument lot or freeze limit")
    values = json.loads(sub.parameters)
    values["quantity"] = body.quantity
    if sub.strategy_id == "time_entry":
        values["side"] = body.side
    sub.parameters = json.dumps(values)
    db.commit()
    return sub_json(db, sub)


@app.get("/api/v1/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db); totals = aggregate_account(db, account.id)
    orders = db.scalars(select(Order).where(Order.account_id == account.id).order_by(Order.created_at.desc()).limit(6)).all()
    subs = db.scalars(select(Subscription).where(Subscription.account_id == account.id)).all()
    session = db.get(SimulatorSession, account.id)
    broker_state = db.get(BrokerState, account.id)
    active = bool(broker_state and broker_state.enabled) if settings.is_021 else bool(session and session.active)
    return {"environment": settings.environment, "currency": settings.currency, "default_symbol": settings.broker_021_default_symbol if settings.is_021 else "RELIANCE", "feed_active": active, "charges_assumed": True, "charge_rate": num(settings.assumed_charge_rate), "account": {"id": account.id, "name": account.name, "kill_state": account.kill_state, "recovered": account.recovered and (not settings.is_021 or (datetime.now(timezone.utc) - utc(account.worker_heartbeat)).total_seconds() < 5), "worker_heartbeat": iso(account.worker_heartbeat)}, "pnl": {k: num(v) for k, v in totals.items() if k != "positions"}, "aggregate_positions": totals["positions"], "running_strategies": sum(1 for s in subs if s.status == "RUNNING"), "subscriptions": len(subs), "recent_orders": [order_json(o) for o in orders]}


@app.get("/api/v1/orders")
def orders(limit: int = Query(100, le=500), user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    return [order_json(o) for o in db.scalars(select(Order).where(Order.account_id == account.id).order_by(Order.created_at.desc()).limit(limit)).all()]


@app.get("/api/v1/trades")
def trades(user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db); rows = db.execute(select(Execution, Order).join(Order, Execution.order_id == Order.id).where(Execution.account_id == account.id).order_by(Execution.executed_at.desc())).all()
    return [{"id": e.id, "execution_id": e.execution_id, "order_id": o.id, "subscription_id": o.subscription_id, "symbol": o.symbol, "side": o.side, "quantity": e.quantity, "price": num(e.price), "charge": num(e.charge), "executed_at": iso(e.executed_at)} for e, o in rows]


@app.get("/api/v1/positions")
def positions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db); result=[]
    for p in db.scalars(select(Position).where(Position.account_id == account.id)).all():
        sub = db.get(Subscription, p.subscription_id); strategy = db.get(Strategy, sub.strategy_id); unrealized, net = position_values(p)
        result.append({"id": p.id, "subscription_id": p.subscription_id, "strategy": strategy.name, "symbol": p.symbol, "quantity": p.quantity, "average_price": num(p.average_price), "last_price": num(p.last_price), "realized_pnl": num(p.realized_pnl), "unrealized_pnl": num(unrealized), "charges": num(p.charges), "net_pnl": num(net)})
    return {"strategy_positions": result, "account": {k: (num(v) if k != "positions" else v) for k, v in aggregate_account(db, account.id).items()}}


@app.get("/api/v1/risk-events")
def risk_events(user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    return [{"id": e.id, "subscription_id": e.subscription_id, "code": e.code, "message": e.message, "created_at": iso(e.created_at)} for e in db.scalars(select(RiskEvent).where(RiskEvent.account_id == account.id).order_by(RiskEvent.created_at.desc()).limit(100)).all()]


@app.get("/api/v1/candles")
def candles(symbol: Optional[str] = None, timeframe: str = "1m", user: User = Depends(current_user), db: Session = Depends(get_db)):
    symbol = symbol or "RELIANCE"
    rows = db.scalars(select(Candle).where(Candle.symbol == symbol, Candle.timeframe == timeframe).order_by(Candle.bucket_start.desc()).limit(100)).all()
    return [{"time": iso(c.bucket_start), "open": num(c.open), "high": num(c.high), "low": num(c.low), "close": num(c.close), "volume": c.volume, "closed": c.closed} for c in reversed(rows)]


@app.get("/api/v1/market-feed")
def market_feed(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A compact, user-visible view of the real market data powering strategies."""
    account = user_account(user, db)
    session = db.get(SimulatorSession, account.id)
    symbols = sorted(set(db.scalars(select(Subscription.symbol).where(Subscription.account_id == account.id)).all()))
    items = []
    for symbol in symbols:
        rows = db.scalars(select(Candle).where(
            Candle.symbol == symbol, Candle.timeframe == "1m"
        ).order_by(Candle.bucket_start.desc()).limit(3)).all()
        current = next((row for row in rows if not row.closed), None)
        closed = next((row for row in rows if row.closed), None)
        def candle_json(row):
            return None if not row else {
                "time": iso(row.bucket_start), "open": num(row.open), "high": num(row.high),
                "low": num(row.low), "close": num(row.close), "volume": row.volume, "closed": row.closed,
            }
        latest = current or closed
        latest_price = num(latest.close) if latest else None
        latest_at = latest.bucket_start if latest else None
        quote_source = "Stored simulator candle"
        if settings.is_021:
            quote = db.get(MarketQuote, symbol)
            latest_price, latest_at = (num(quote.price), quote.market_at) if quote else (None, None)
            quote_source = "021 sampled live market updates"
        is_stale = bool(latest_at and datetime.now(timezone.utc) - utc(latest_at) > timedelta(seconds=settings.market_stale_seconds if settings.is_021 else 120))
        items.append({"symbol": symbol, "latest_price": latest_price, "latest_at": iso(latest_at), "stale": is_stale, "quote_source": quote_source, "current_candle": candle_json(current), "last_closed_candle": candle_json(closed)})
    return {
        "provider": "021 NSE live feed" if settings.is_021 else "Deterministic simulator",
        "active": bool(db.get(BrokerState, account.id) and db.get(BrokerState, account.id).market_connected and (datetime.now(timezone.utc) - utc(account.worker_heartbeat)).total_seconds() < 5) if settings.is_021 else bool(session and session.active),
        "symbols": items,
    }


@app.post("/api/v1/simulator/orders")
def simulator_order(body: SimOrderBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if settings.is_021: raise HTTPException(409, "Simulator orders are disabled in 021 mode")
    account = user_account(user, db); sub = owned_sub(db, account.id, body.subscription_id)
    return order_json(submit_order(db, account, sub, body.side, body.quantity, body.price, body.scenario, body.client_order_id))


@app.post("/api/v1/simulator/demo")
def simulator_demo(body: DemoBody = DemoBody(), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if settings.is_021: raise HTTPException(409, "Simulator demo is disabled in 021 mode")
    account = user_account(user, db); subs = db.scalars(select(Subscription).where(Subscription.account_id == account.id)).all()
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    if body.scenario != "vertical":
        if not subs: raise HTTPException(409, "Subscribe to at least one strategy first")
        sub = subs[0]
        if sub.status != "RUNNING": sub.status = "RUNNING"; db.commit()
        if body.scenario == "stale_feed":
            order = reject_risk(db, account.id, sub.id, "STALE_MARKET", "Market data is stale; new exposure is blocked", f"{run_id}-stale", sub.symbol, "BUY", 10)
            record_simulation_event(db, account.id, "RISK_REJECTED", "Stale market data blocked order", "No broker request was made", run_id); db.commit()
        else:
            order = submit_order(db, account, sub, "BUY", 10, d("2550"), body.scenario, client_id=f"{run_id}-{sub.id}")
            record_simulation_event(db, account.id, "SCENARIO", f"{body.scenario.replace('_', ' ')} scenario completed", f"Order #{order.id}", run_id); db.commit()
        return {"run_id": run_id, "message": f"{body.scenario} simulator scenario completed", "orders": [order_json(order)]}
    if len(subs) < 3: raise HTTPException(409, "Subscribe to all three strategies first")
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=12)
    prices = [2500, 2496, 2492, 2498, 2504, 2512, 2520, 2514, 2507, 2499, 2518, 2532, 2544, 2550]
    for index, price in enumerate(prices): ingest_tick(db, "RELIANCE", d(price), base + timedelta(minutes=index), 100 + index)
    record_simulation_event(db, account.id, "CANDLES", "14 deterministic ticks accepted", "Closed 1-minute and 5-minute candles were generated", run_id)
    results=[]
    for i, sub in enumerate(subs[:3]):
        if sub.status != "RUNNING": sub.status = "RUNNING"; db.commit()
        signal = evaluate_strategy(db, sub)
        side, quantity = (signal[0], signal[1]) if signal else (("SELL" if i == 1 else "BUY"), [10, 8, 12][i])
        record_simulation_event(db, account.id, "SIGNAL", f"{sub.name if hasattr(sub, 'name') else sub.strategy_id} emitted {side}", "Closed-candle strategy evaluation", run_id)
        results.append(order_json(submit_order(db, account, sub, side, quantity, d(prices[-1] + i), "partial" if i == 0 else "full", client_id=f"demo-{account.id}-{run_id}-{sub.id}")))
    # Deliberate risk violation demonstrates enforcement outside strategy code.
    results.append(order_json(submit_order(db, account, subs[0], "BUY", subs[0].max_position_size + 1, d(prices[-1]), client_id=f"demo-risk-{account.id}-{run_id}")))
    record_simulation_event(db, account.id, "RUN_COMPLETE", "Vertical demo completed", "Candles, signals, fills, positions and risk rejection recorded", run_id); db.commit()
    return {"run_id": run_id, "message": "Deterministic candle, partial-fill, opposing-position and risk scenarios completed", "orders": results}


@app.get("/api/v1/simulator/events")
def simulator_events(user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    rows = db.scalars(select(SimulationEvent).where(SimulationEvent.account_id == account.id).order_by(SimulationEvent.created_at.desc()).limit(100)).all()
    return [{"id": row.id, "run_id": row.run_id, "type": row.event_type, "title": row.title, "detail": row.detail, "created_at": iso(row.created_at)} for row in reversed(rows)]


@app.post("/api/v1/simulator/stream/{action}")
def simulator_stream(action: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if settings.is_021: raise HTTPException(409, "Use /broker/stream in 021 mode")
    if action not in {"start", "stop"}: raise HTTPException(404, "Unknown stream action")
    account = user_account(user, db)
    session = db.get(SimulatorSession, account.id) or SimulatorSession(account_id=account.id)
    session.active = action == "start"
    if session.active and not session.virtual_time:
        session.virtual_time = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    label = "Virtual market stream"
    detail = "One simulated minute advances every two seconds"
    db.add(session); record_simulation_event(db, account.id, "STREAM", f"{label} {action}ed", detail)
    db.commit()
    return {"active": session.active, "virtual_time": iso(session.virtual_time)}


@app.post("/api/v1/accounts/{account_id}/kill-switch")
def kill(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    if account.id != account_id: raise HTTPException(403, "Account access denied")
    if settings.is_021:
        bound_account(user, db)
        request_kill(db, account, user.id)
        return {"state": account.kill_state, "elapsed_ms": 0}
    started = datetime.now(timezone.utc); perform_kill(db, account, user.id)
    return {"state": account.kill_state, "elapsed_ms": int((datetime.now(timezone.utc)-started).total_seconds()*1000)}


@app.post("/api/v1/accounts/{account_id}/resume")
def resume(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = user_account(user, db)
    if account.id != account_id: raise HTTPException(403, "Account access denied")
    remaining = db.scalar(select(func.count(Position.id)).where(Position.account_id == account.id, Position.quantity != 0)) or 0
    unknown = db.scalar(select(func.count(Order.id)).where(Order.account_id == account.id, Order.status.in_(OPEN_STATES))) or 0
    if remaining or unknown: raise HTTPException(409, "Reconciliation is not flat and resolved")
    if settings.is_021:
        bound_account(user, db)
        if not account.recovered or account.kill_state != "HALTED" or (datetime.now(timezone.utc) - utc(account.worker_heartbeat)).total_seconds() > 5:
            raise HTTPException(409, "Worker must confirm a flat reconciled account before resuming")
    account.kill_state = "RUNNING"; account.recovered = True; db.add(AuditEvent(account_id=account.id, actor_user_id=user.id, action="KILL_RESET", detail="Explicit reset after reconciliation")); db.commit()
    return {"state": account.kill_state}


@app.websocket("/api/v1/ws/accounts/{account_id}")
async def account_ws(websocket: WebSocket, account_id: int, token: str):
    await websocket.accept()
    with SessionLocal() as db:
        digest = hashlib.sha256(token.encode()).hexdigest(); session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest)); account = db.get(Account, account_id)
        if not session or not account or account.user_id != session.user_id:
            await websocket.close(code=4403); return
    sequence = 0
    try:
        while True:
            sequence += 1
            await websocket.send_json({"type": "connection.updated", "account_id": account_id, "timestamp": datetime.now(timezone.utc).isoformat(), "sequence": sequence, "payload": {"status": "connected"}})
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
