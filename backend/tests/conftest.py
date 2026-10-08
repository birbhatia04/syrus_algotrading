import os
os.environ["DATABASE_URL"] = "sqlite:///./test_aegis.db"

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
