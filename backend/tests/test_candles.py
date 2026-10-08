from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Candle
from app.services import ingest_tick


def test_utc_candle_boundaries_and_late_tick_policy():
    base = datetime(2026, 1, 1, 9, 17, 20, tzinfo=timezone.utc)
    with SessionLocal() as db:
        ingest_tick(db, "TEST", Decimal("100"), base, 10)
        ingest_tick(db, "TEST", Decimal("102"), base + timedelta(seconds=20), 5)
        ingest_tick(db, "TEST", Decimal("99"), base + timedelta(minutes=1), 2)
        one = db.scalars(select(Candle).where(Candle.symbol == "TEST", Candle.timeframe == "1m").order_by(Candle.bucket_start)).all()
        assert len(one) == 2
        assert (one[0].open, one[0].high, one[0].low, one[0].close, one[0].volume) == (Decimal("100.0000"), Decimal("102.0000"), Decimal("100.0000"), Decimal("102.0000"), 15)
        assert one[0].closed is True
        ingest_tick(db, "TEST", Decimal("200"), base + timedelta(seconds=30), 1)
        db.refresh(one[0])
        assert one[0].high == Decimal("102.0000")
