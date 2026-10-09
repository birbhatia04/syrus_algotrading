import os
os.environ["DATABASE_URL"] = "sqlite:///./test_aegis.db"
# Tests cover deterministic simulator behaviour and must never inherit the
# developer's local broker selection or credentials.
os.environ["ENVIRONMENT"] = "SIMULATOR"
# Pin the documented NSE session so tests stay hermetic when a deployment
# widens the window for the sandbox feed.
os.environ["MARKET_OPEN_IST"] = "09:15"
os.environ["MARKET_CLOSE_IST"] = "15:15"
os.environ["TIME_ENTRY_WINDOW_SECONDS"] = "60"

import pytest
from fastapi.testclient import TestClient
from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from app.database import SessionLocal
    from app.services import seed_strategies
    with SessionLocal() as db:
        seed_strategies(db)
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth(client):
    response = client.post("/api/v1/auth/register", json={"email": "test@example.com", "password": "strong-password"})
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}
