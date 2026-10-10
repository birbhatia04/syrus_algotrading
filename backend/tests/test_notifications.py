from datetime import datetime, timezone

from app.config import settings
from app.database import SessionLocal
from app.models import Order, RiskEvent, Subscription


def test_daily_cycle_limit_notification_for_running_strategy(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "environment", "021_SANDBOX")
    monkeypatch.setattr(settings, "market_open_ist", "00:00")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        sub = Subscription(account_id=1, strategy_id="ma_cross", symbol="RELIANCE",
                           status="RUNNING", parameters='{"quantity": 1}')
        db.add(sub)
        db.flush()
        for index in range(6):
            db.add(Order(account_id=1, subscription_id=sub.id, client_order_id=f"cycle-{index}",
                         symbol="RELIANCE", side="BUY", requested_qty=1, filled_qty=1,
                         status="FILLED", created_at=now))
        db.commit()
        sub_id = sub.id

    notices = client.get("/api/v1/notifications", headers=auth).json()
    limit = next(item for item in notices if item["id"].startswith("cycle-limit-"))
    assert limit["title"] == "Daily cycle limit reached"
    assert "6 entries today" in limit["message"]
    subscriptions = client.get("/api/v1/subscriptions", headers=auth).json()
    assert subscriptions[0]["cycle_count"] == 6
    assert subscriptions[0]["cycle_limit"] == 6

    with SessionLocal() as db:
        db.get(Subscription, sub_id).status = "PAUSED"
        db.commit()
    assert not any(item["id"].startswith("cycle-limit-")
                   for item in client.get("/api/v1/notifications", headers=auth).json())


def test_recent_risk_and_partial_fill_notifications(client, auth):
    with SessionLocal() as db:
        sub = Subscription(account_id=1, strategy_id="ma_cross", symbol="RELIANCE",
                           status="PAUSED", parameters='{"quantity": 10}')
        db.add(sub)
        db.flush()
        db.add(RiskEvent(account_id=1, subscription_id=sub.id, code="POSITION_LIMIT",
                         message="Position limit reached"))
        db.add(Order(account_id=1, subscription_id=sub.id, client_order_id="partial",
                     symbol="RELIANCE", side="BUY", requested_qty=10, filled_qty=4,
                     reserved_qty=6, status="PARTIALLY_FILLED"))
        db.commit()

    notices = client.get("/api/v1/notifications", headers=auth).json()
    assert any(item["title"] == "Trade blocked by risk" for item in notices)
    assert any(item["title"] == "Order partially filled" and "4/10" in item["message"]
               for item in notices)
