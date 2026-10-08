from datetime import datetime, timedelta, timezone
from app.main import iso


def test_api_timestamps_always_include_utc_marker():
    # SQLite commonly returns this as naive even though the durable convention is UTC.
    stored_utc = datetime(2026, 10, 8, 8, 59, 0)
    assert iso(stored_utc) == "2026-10-08T08:59:00Z"

    ist = timezone(timedelta(hours=5, minutes=30))
    assert iso(datetime(2026, 10, 8, 14, 29, 0, tzinfo=ist)) == "2026-10-08T08:59:00Z"
