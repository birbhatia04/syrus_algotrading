from decimal import Decimal
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Account, Execution, Position, Subscription
from app.services import apply_fill, submit_order


def setup_running(client, auth):
    strategies = client.get("/api/v1/strategies", headers=auth).json()
    subs = []
    for strategy in strategies:
        sub = client.post("/api/v1/subscriptions", headers=auth, json={"strategy_id": strategy["id"], "symbol": "RELIANCE"}).json()
        client.post(f"/api/v1/subscriptions/{sub['id']}/start", headers=auth)
        subs.append(sub)
    return subs


def test_partial_fill_and_duplicate_execution_are_exact(client, auth):
    sub = setup_running(client, auth)[0]
    order = client.post("/api/v1/simulator/orders", headers=auth, json={"subscription_id": sub["id"], "side": "BUY", "quantity": 100, "price": 100, "scenario": "pending"}).json()
    with SessionLocal() as db:
        from app.models import Order
        row = db.get(Order, order["id"])
        assert apply_fill(db, row, "execution-1", 40, Decimal("100")) is True
        assert apply_fill(db, row, "execution-1", 40, Decimal("100")) is False
        assert apply_fill(db, row, "execution-2", 60, Decimal("101")) is True
        position = db.scalar(select(Position).where(Position.subscription_id == sub["id"]))
        assert position.quantity == 100
        assert position.average_price == Decimal("100.6000")
        assert db.query(Execution).count() == 2


def test_partial_scenario_leaves_the_unfilled_balance_pending(client, auth):
    sub = setup_running(client, auth)[0]
    order = client.post("/api/v1/simulator/orders", headers=auth, json={
        "subscription_id": sub["id"], "side": "BUY", "quantity": 10, "price": 100, "scenario": "partial"
    }).json()
    assert order["status"] == "PARTIALLY_FILLED"
    assert order["filled_qty"] == 4
    assert order["requested_qty"] == 10


def test_opposing_strategies_reconcile_and_kill_flattens(client, auth):
    first, second, *_ = setup_running(client, auth)
    client.post("/api/v1/simulator/orders", headers=auth, json={"subscription_id": first["id"], "side": "BUY", "quantity": 40, "price": 100, "scenario": "full"})
    client.post("/api/v1/simulator/orders", headers=auth, json={"subscription_id": second["id"], "side": "SELL", "quantity": 25, "price": 100, "scenario": "full"})
    state = client.get("/api/v1/positions", headers=auth).json()
    assert sorted(p["quantity"] for p in state["strategy_positions"]) == [-25, 40]
    assert state["account"]["positions"]["RELIANCE"] == 15
    account_id = client.get("/api/v1/me", headers=auth).json()["account"]["id"]
    result = client.post(f"/api/v1/accounts/{account_id}/kill-switch", headers=auth).json()
    assert result["state"] == "HALTED"
    assert all(p["quantity"] == 0 for p in client.get("/api/v1/positions", headers=auth).json()["strategy_positions"])


def test_pending_reservation_enforces_position_limit(client, auth):
    sub = setup_running(client, auth)[0]
    client.patch(f"/api/v1/subscriptions/{sub['id']}/risk-limits", headers=auth, json={"max_daily_loss": 1000, "max_position_size": 100, "max_orders_per_minute": 10})
    first = client.post("/api/v1/simulator/orders", headers=auth, json={"subscription_id": sub["id"], "side": "BUY", "quantity": 80, "price": 100, "scenario": "pending"}).json()
    second = client.post("/api/v1/simulator/orders", headers=auth, json={"subscription_id": sub["id"], "side": "BUY", "quantity": 30, "price": 100, "scenario": "full"}).json()
    assert first["status"] == "ACKNOWLEDGED"
    assert second["status"] == "RISK_REJECTED"
    assert "exceeds limit" in second["reason"]


def test_demo_runs_are_repeatable_and_timeline_is_persisted(client, auth):
    setup_running(client, auth)
    first = client.post("/api/v1/simulator/demo", headers=auth, json={"scenario": "vertical"}).json()
    second = client.post("/api/v1/simulator/demo", headers=auth, json={"scenario": "vertical"}).json()
    assert first["run_id"] != second["run_id"]
    assert first["orders"][0]["client_order_id"] != second["orders"][0]["client_order_id"]
    timeline = client.get("/api/v1/simulator/events", headers=auth).json()
    assert any(event["type"] == "CANDLES" for event in timeline)
    assert any(event["type"] == "RUN_COMPLETE" for event in timeline)


def test_halted_account_can_be_explicitly_resumed_when_flat(client, auth):
    first, *_ = setup_running(client, auth)
    client.post("/api/v1/simulator/orders", headers=auth, json={"subscription_id": first["id"], "side": "BUY", "quantity": 10, "price": 100, "scenario": "full"})
    account_id = client.get("/api/v1/me", headers=auth).json()["account"]["id"]
    assert client.post(f"/api/v1/accounts/{account_id}/kill-switch", headers=auth).json()["state"] == "HALTED"
    assert client.post(f"/api/v1/accounts/{account_id}/resume", headers=auth).json()["state"] == "RUNNING"


def test_profile_completion_is_persisted(client, auth):
    assert client.get("/api/v1/me", headers=auth).json()["profile_complete"] is False
    response = client.put("/api/v1/profile", headers=auth, json={
        "full_name": "Test User",
        "phone": "+91 98765 43210",
        "city": "Mumbai",
        "trading_experience": "BEGINNER",
        "risk_profile": "BALANCED",
    })
    assert response.status_code == 200
    me = client.get("/api/v1/me", headers=auth).json()
    assert me["profile_complete"] is True
    assert me["profile"]["full_name"] == "Test User"


def test_itemised_intraday_charges_follow_the_published_schedule():
    from app.charges import compute_charges
    buy = compute_charges("BUY", Decimal("100"), 10)
    sell = compute_charges("SELL", Decimal("100"), 10)
    assert buy.stt == Decimal("0") and buy.stamp_duty > 0
    assert sell.stamp_duty == Decimal("0") and sell.stt > 0
    assert buy.total == buy.brokerage + buy.stt + buy.exchange_txn + buy.sebi + buy.ipft + buy.stamp_duty + buy.gst
    assert sell.total == sell.brokerage + sell.stt + sell.exchange_txn + sell.sebi + sell.ipft + sell.stamp_duty + sell.gst
    capped = compute_charges("BUY", Decimal("1000000"), 1)
    assert capped.brokerage == Decimal("20")
