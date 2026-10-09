import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import struct
import httpx
import pytest
from sqlalchemy import select
from app.brokers.broker_021 import Broker021Adapter, BrokerError
from app.brokers.feed_021 import decode_market, decode_order_event, EPOCH_OFFSET
from app.config import settings
from app.database import SessionLocal
from app.engine_021 import TradingEngine
from app.models import Account, BrokerState, Candle, Execution, Instrument, MarketQuote, Order, OrderRoute, Position, StrategyDay, Subscription, User
from app.services import apply_fill, seed_strategies
from app.trading_rules import day_ledger, risk_reason, strategy_decision
from app.broker_api import request_kill

NOW = datetime(2026, 10, 9, 4, 0, tzinfo=timezone.utc)  # 09:30 IST


@pytest.fixture
def live_setup(monkeypatch):
    monkeypatch.setattr(settings, "environment", "021_SANDBOX")
    monkeypatch.setattr(settings, "broker_021_account_id", 1)
    monkeypatch.setattr(settings, "broker_021_access_token", "test-only")
    with SessionLocal() as db:
        seed_strategies(db)
        db.add(User(id=1, email="021@example.com", password_hash="unused"))
        db.flush()
        db.add(Account(id=1, user_id=1, recovered=True, kill_state="RUNNING"))
        db.flush()
        db.add(BrokerState(account_id=1, enabled=True, market_connected=True, orders_connected=True, last_reconciled_at=NOW))
        db.add(Instrument(symbol="RELIANCE", token=2885, ticksize=5, lot_size=1, freeze_quantity=10000,
                          lower_circuit=9000, upper_circuit=11000, trading_day="2026-10-09"))
        db.add(MarketQuote(symbol="RELIANCE", price=101, day_open=100, market_at=NOW, received_at=NOW, cumulative_volume=1000))
        for i, strategy in enumerate(["time_entry", "open_breakout", "ma_cross"], 1):
            db.add(Subscription(id=i, account_id=1, strategy_id=strategy, symbol="RELIANCE",
                                status="RUNNING", parameters=json.dumps({"quantity": 10, "side": "BUY"})))
        db.commit()


def context(db, sub_id=1):
    return (db.get(Account,1),db.get(BrokerState,1),db.get(Subscription,sub_id),
            db.get(Instrument,"RELIANCE"),db.get(MarketQuote,"RELIANCE"))


class FakeBroker:
    def __init__(self):
        self.orders = []
        self.trades = {}
        self.net = 0
        self.calls = []
        self.failure = None

    async def list_orders(self): return self.orders
    async def list_trades(self): return [t for trades in self.trades.values() for t in trades]
    async def positions(self):
        return [{"token":2885,"exchange":"NSECM","product":"INTRADAY","netQuantity":self.net,"squareOffQuantity":-self.net}]
    async def order_trades(self, oid): return self.trades.get(oid, [])
    async def place_order(self, token, quantity, price_paise=0):
        self.calls.append((token,quantity,price_paise))
        if self.failure: raise self.failure
        oid = str(len(self.orders)+100)
        self.orders.append({"orderId":oid,"time":int(NOW.timestamp()),"token":token,"exchange":"NSECM",
                            "product":"INTRADAY","qtyRemaining":quantity,"qtyTraded":0,"status":"Placed","reason":""})
        return oid
    async def cancel_order(self, oid, token):
        # Late execution during cancellation, followed by a real REST cancellation.
        row = next(o for o in self.orders if str(o["orderId"])==oid)
        row["status"] = "Cancelled"
    def fill(self, oid, quantity, price=10000):
        trades = self.trades.setdefault(oid, [])
        trades.append({"tradeId":1000+sum(map(len,self.trades.values())),"tradeTime":int(NOW.timestamp()),
                       "quantity":quantity,"token":2885,"exchange":"NSECM","product":"INTRADAY","price":price})
        row = next(o for o in self.orders if str(o["orderId"])==oid)
        row["qtyRemaining"] -= quantity
        row["qtyTraded"] += quantity
        row["status"] = "Executed" if not row["qtyRemaining"] else "Pending"
        self.net += quantity


def test_market_binary_offsets_snapshot_suffix_and_concatenation():
    full = bytearray(220)
    struct.pack_into(">HHII", full, 0, 3, 1, 2885, 141400)
    struct.pack_into(">Q", full, 20, 9876543210)
    struct.pack_into(">I", full, 48, 140000)
    struct.pack_into(">I", full, 60, int(NOW.timestamp())-EPOCH_OFFSET)
    snapshot = struct.pack(">HHIIcI",1,1,22,12345,b"o",12000)
    ticks = decode_market(bytes(full)+snapshot+b"\x00\x0a", NOW)
    assert ticks[0].volume == 9876543210
    assert ticks[0].timestamp == NOW
    assert ticks[0].open_paise == 140000
    assert ticks[1].token == 22 and ticks[1].open_paise == 12000
    with pytest.raises(ValueError): decode_market(bytes(full[:-1]))


def test_order_socket_signed_quantity_and_no_trade_id_inference():
    frame=bytearray(46)
    struct.pack_into(">HH",frame,0,4,1)
    struct.pack_into(">IiI",frame,34,1042,-4,140000)
    event=decode_order_event(bytes(frame))
    assert event["quantity"] == -4 and event["order_id"] == "1042"
    assert "trade_id" not in event
    assert decode_order_event(b"\x00\x0a") is None


def test_rest_units_envelopes_and_ambiguous_write_no_retry():
    requests=[]
    def respond(request):
        requests.append(request)
        if request.method=="POST":
            assert json.loads(request.content)=={"exchange":"NSE","token":2885,"qty":-3,"price":0,"book":"RL","product":"INTRADAY","validity":"Day"}
            return httpx.Response(503,json={"success":False,"error":"unavailable"})
        return httpx.Response(200,json=[])
    async def scenario():
        broker=Broker021Adapter("test",transport=httpx.MockTransport(respond))
        with pytest.raises(BrokerError) as caught: await broker.place_order(2885,-3)
        assert caught.value.ambiguous
        assert await broker.list_orders()==[]
        await broker.close()
    asyncio.run(scenario())
    assert len(requests)==2


def test_ambiguous_submission_and_restart_never_resend(live_setup):
    broker=FakeBroker()
    broker.failure=BrokerError("timeout",ambiguous=True)
    engine=TradingEngine(broker,1)
    asyncio.run(engine.submit(1,10,"test-intent",NOW))
    engine.startup()
    asyncio.run(engine.submit(1,10,"test-intent",NOW))
    with SessionLocal() as db:
        order=db.scalar(select(Order))
        assert order.status=="UNKNOWN" and order.reserved_qty==10
        assert not db.get(Account,1).recovered
        assert db.get(OrderRoute,order.id)
    assert len(broker.calls)==1


def test_partial_fills_replay_and_opposing_strategy_attribution(live_setup):
    broker=FakeBroker(); engine=TradingEngine(broker,1)
    asyncio.run(engine.submit(1,10,"long",NOW))
    broker.fill("100",4)
    assert asyncio.run(engine.reconcile(NOW))
    assert asyncio.run(engine.reconcile(NOW))
    with SessionLocal() as db:
        order=db.scalar(select(Order))
        assert order.filled_qty==4 and order.reserved_qty==6
        assert db.scalar(select(Position)).charges==Decimal("0.2000")
        assert len(db.scalars(select(Execution)).all())==1
    asyncio.run(engine.submit(2,-3,"short",NOW))
    broker.fill("101",-3,10100)
    broker.fill("100",6,10200)
    assert asyncio.run(engine.reconcile(NOW))
    with SessionLocal() as db:
        assert sorted(p.quantity for p in db.scalars(select(Position)).all())==[-3,10]
        assert broker.net==7
        assert len(db.scalars(select(Execution)).all())==3
    assert asyncio.run(engine.reconcile(NOW))


def test_cancel_race_kill_waits_for_fills_then_flattens(live_setup):
    broker=FakeBroker(); engine=TradingEngine(broker,1)
    asyncio.run(engine.submit(1,10,"entry",NOW))
    broker.fill("100",4)
    asyncio.run(engine.reconcile(NOW))
    with SessionLocal() as db:
        request_kill(db,db.get(Account,1),1)
        state=db.get(BrokerState,1);state.kill_started_at=NOW;db.commit()
    asyncio.run(engine.advance(NOW))
    # Cancel sent; no closing order until REST acknowledges the racing fill.
    assert len(broker.calls)==1
    broker.fill("100",2);broker.orders[0]["status"]="Cancelled"
    asyncio.run(engine.reconcile(NOW+timedelta(seconds=1)))
    asyncio.run(engine.advance(NOW+timedelta(seconds=1)))
    assert broker.calls[-1][1]==-6
    broker.fill("101",-6,9900)
    asyncio.run(engine.reconcile(NOW+timedelta(seconds=2)))
    asyncio.run(engine.advance(NOW+timedelta(seconds=2)))
    with SessionLocal() as db:
        assert db.get(Account,1).kill_state=="HALTED"
        assert db.scalar(select(Position)).quantity==0
        assert db.get(BrokerState,1).kill_elapsed_ms==2000


def test_external_position_mismatch_blocks_entries(live_setup):
    broker=FakeBroker();broker.net=99;engine=TradingEngine(broker,1)
    assert not asyncio.run(engine.reconcile(NOW))
    asyncio.run(engine.submit(1,10,"blocked",NOW))
    assert not broker.calls


def test_position_reservations_include_unknown_and_cancel_pending(live_setup):
    with SessionLocal() as db:
        account,state,sub,instrument,quote=context(db)
        sub.max_position_size=10
        db.add(Order(account_id=1,subscription_id=1,client_order_id="pending",symbol="RELIANCE",side="BUY",
                     requested_qty=8,reserved_qty=8,status="CANCEL_PENDING"))
        db.flush()
        assert risk_reason(db,account,state,sub,instrument,quote,3,NOW)[0]=="POSITION_LIMIT"
        assert risk_reason(db,account,state,sub,instrument,quote,-3,NOW) is None


def test_daily_loss_latches_and_rolls_over_at_ist_midnight(live_setup):
    with SessionLocal() as db:
        account,state,sub,instrument,quote=context(db)
        sub.max_daily_loss=100
        ledger=day_ledger(db,sub.id,NOW);ledger.realized_pnl=-100
        assert risk_reason(db,account,state,sub,instrument,quote,1,NOW)[0]=="DAILY_LOSS"
        ledger.realized_pnl=1000
        assert risk_reason(db,account,state,sub,instrument,quote,1,NOW)[0]=="DAILY_LOSS"
        assert not day_ledger(db,sub.id,datetime(2026,10,9,18,30,tzinfo=timezone.utc)).loss_latched


def test_close_only_rejects_exposure_increase(live_setup):
    with SessionLocal() as db:
        account,state,sub,instrument,quote=context(db)
        db.add(Position(account_id=1,subscription_id=1,symbol="RELIANCE",quantity=4,average_price=100,last_price=100))
        db.flush()
        assert risk_reason(db,account,state,sub,instrument,quote,1,NOW,True)[0]=="CLOSE_ONLY"
        assert risk_reason(db,account,state,sub,instrument,quote,-5,NOW,True)[0]=="CLOSE_ONLY"
        assert risk_reason(db,account,state,sub,instrument,quote,-4,NOW,True) is None


def test_stale_feed_and_rolling_rate_limit(live_setup):
    with SessionLocal() as db:
        account,state,sub,instrument,quote=context(db)
        quote.market_at=NOW-timedelta(seconds=16)
        assert risk_reason(db,account,state,sub,instrument,quote,1,NOW)[0]=="STALE_MARKET"
        quote.market_at=NOW
        sub.max_orders_per_minute=1
        db.add(Order(account_id=1,subscription_id=1,client_order_id="rate",symbol="RELIANCE",side="BUY",
                     requested_qty=1,status="REJECTED",created_at=NOW-timedelta(seconds=59)))
        db.flush()
        assert risk_reason(db,account,state,sub,instrument,quote,1,NOW)[0]=="ORDER_RATE"


def test_required_time_strategy_and_missed_window(live_setup):
    with SessionLocal() as db:
        _,_,sub,instrument,quote=context(db)
        open_time=NOW.replace(hour=3,minute=45)
        quote.market_at=quote.received_at=open_time
        decision=strategy_decision(db,sub,quote,instrument,open_time)
        assert decision.signed_qty==10
        quote.market_at=quote.received_at=NOW
        assert strategy_decision(db,sub,quote,instrument,NOW) is None
        assert strategy_decision(db,sub,quote,instrument,NOW.replace(hour=9,minute=45)).close_only


@pytest.mark.parametrize("quantity,price", [(2,106.05),(2,95.95),(-2,95.95),(-2,106.05)])
def test_breakout_target_and_stop_use_actual_entry_fill(live_setup,quantity,price):
    with SessionLocal() as db:
        _,_,sub,instrument,quote=context(db,2)
        assert strategy_decision(db,sub,quote,instrument,NOW).signed_qty==10
        db.add(Position(account_id=1,subscription_id=2,symbol="RELIANCE",quantity=quantity,average_price=101,last_price=price))
        db.flush()
        quote.price=Decimal(str(price))
        decision=strategy_decision(db,sub,quote,instrument,NOW)
        assert decision.close_only and decision.signed_qty==-quantity


def test_candle_strategy_uses_both_timeframes(live_setup):
    with SessionLocal() as db:
        _,_,sub,instrument,quote=context(db,3)
        prices=[100,100,100,100,100,99,99,105]
        for i,price in enumerate(prices):
            db.add(Candle(symbol="RELIANCE",timeframe="1m",bucket_start=NOW-timedelta(minutes=8-i),
                          open=price,high=price,low=price,close=price,closed=True))
        db.flush()
        assert strategy_decision(db,sub,quote,instrument,NOW) is None
        db.add(Candle(symbol="RELIANCE",timeframe="5m",bucket_start=NOW-timedelta(minutes=10),
                      open=100,high=102,low=99,close=102,closed=True))
        db.flush()
        assert strategy_decision(db,sub,quote,instrument,NOW).signed_qty==10


def test_kill_timeout_reports_attention_not_false_success(live_setup):
    with SessionLocal() as db:
        account=db.get(Account,1);account.kill_state="CANCELLING";account.recovered=False
        state=db.get(BrokerState,1);state.kill_started_at=NOW-timedelta(seconds=11)
        db.commit()
    asyncio.run(TradingEngine(FakeBroker(),1).advance(NOW))
    with SessionLocal() as db:
        assert db.get(Account,1).kill_state=="NEEDS_ATTENTION"


def test_kill_does_not_call_broken_strategy_code(live_setup, monkeypatch):
    def broken(*args):
        raise AssertionError("Emergency handling must not invoke strategy code")
    monkeypatch.setattr("app.engine_021.strategy_decision", broken)
    with SessionLocal() as db:
        account=db.get(Account,1);account.kill_state="KILL_REQUESTED"
        db.get(BrokerState,1).kill_started_at=NOW
        db.commit()
    asyncio.run(TradingEngine(FakeBroker(),1).advance(NOW))
    with SessionLocal() as db:
        assert db.get(Account,1).kill_state=="HALTED"


def test_strategy_exception_pauses_and_closes_only_its_position(live_setup, monkeypatch):
    def broken(*args): raise RuntimeError("strategy failed")
    monkeypatch.setattr("app.engine_021.strategy_decision", broken)
    broker=FakeBroker();engine=TradingEngine(broker,1)
    asyncio.run(engine.submit(1,10,"entry",NOW));broker.fill("100",10)
    asyncio.run(engine.reconcile(NOW))
    asyncio.run(engine.advance(NOW))
    assert broker.calls[-1][1]==-10
    with SessionLocal() as db:
        assert db.get(Subscription,1).status=="PAUSED"


def test_large_close_is_chunked_at_exchange_freeze_limit(live_setup):
    broker=FakeBroker();engine=TradingEngine(broker,1)
    asyncio.run(engine.submit(1,10,"entry",NOW));broker.fill("100",10)
    asyncio.run(engine.reconcile(NOW))
    with SessionLocal() as db:
        db.get(Instrument,"RELIANCE").freeze_quantity=6
        db.get(Account,1).kill_state="KILL_REQUESTED"
        db.get(BrokerState,1).kill_started_at=NOW
        db.commit()
    asyncio.run(engine.advance(NOW))
    assert broker.calls[-1][1]==-6


def test_feed_replays_do_not_double_candle_volume(live_setup):
    from app.brokers.feed_021 import MarketTick
    engine=TradingEngine(FakeBroker(),1)
    tick=MarketTick(1,2885,10200,10000,1010,NOW+timedelta(seconds=1))
    engine.accept_ticks([tick,tick],NOW+timedelta(seconds=1))
    with SessionLocal() as db:
        assert db.scalar(select(Candle).where(Candle.timeframe=="1m")).volume==10
        assert db.get(MarketQuote,"RELIANCE").price==102


def test_live_api_cannot_invoke_simulator_or_trade_another_account(client,auth,monkeypatch):
    monkeypatch.setattr(settings,"environment","021_SANDBOX")
    monkeypatch.setattr(settings,"broker_021_account_id",99)
    assert client.post("/api/v1/simulator/demo",headers=auth,json={}).status_code==409
    assert client.post("/api/v1/simulator/stream/start",headers=auth).status_code==409
    assert client.post("/api/v1/broker/stream/start",headers=auth).status_code==403
    with SessionLocal() as db:
        db.get(Account,1).worker_heartbeat=datetime.now(timezone.utc)-timedelta(seconds=20)
        db.commit()
    assert client.get("/api/v1/dashboard",headers=auth).json()["account"]["recovered"] is False


def test_rejection_releases_reservation_without_fabricating_fill(live_setup):
    broker=FakeBroker();broker.failure=BrokerError("Insufficient funds")
    asyncio.run(TradingEngine(broker,1).submit(1,10,"reject",NOW))
    with SessionLocal() as db:
        order=db.scalar(select(Order))
        assert order.status=="REJECTED" and order.reserved_qty==0 and order.filled_qty==0
        assert not db.scalars(select(Execution)).all()


def test_runaway_strategy_cannot_bypass_platform_position_limit(live_setup,monkeypatch):
    from app.trading_rules import Decision
    monkeypatch.setattr("app.engine_021.strategy_decision",lambda *args: Decision(10001,"runaway"))
    broker=FakeBroker()
    asyncio.run(TradingEngine(broker,1).advance(NOW))
    assert not broker.calls
    with SessionLocal() as db:
        assert not db.scalars(select(Order)).all()


def test_restart_applies_missed_rest_fills_once(live_setup):
    broker=FakeBroker();before=TradingEngine(broker,1)
    asyncio.run(before.submit(1,10,"restart",NOW))
    broker.fill("100",10)
    after=TradingEngine(broker,1);assert after.startup()
    assert asyncio.run(after.reconcile(NOW))
    assert asyncio.run(after.reconcile(NOW))
    with SessionLocal() as db:
        assert db.scalar(select(Position)).quantity==10
        assert len(db.scalars(select(Execution)).all())==1


def test_missing_fill_detail_blocks_even_if_order_reports_executed(live_setup):
    broker=FakeBroker();engine=TradingEngine(broker,1)
    asyncio.run(engine.submit(1,10,"missing",NOW))
    broker.orders[0].update(qtyTraded=10,qtyRemaining=0,status="Executed")
    broker.net=10
    assert not asyncio.run(engine.reconcile(NOW))
    with SessionLocal() as db:
        assert not db.get(Account,1).recovered
        assert db.scalar(select(Order)).filled_qty==0
        assert not db.scalars(select(Execution)).all()
