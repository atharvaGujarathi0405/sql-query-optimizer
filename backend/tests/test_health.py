# Integration test: needs the MySQL container running (docker compose up -d).
from fastapi.testclient import TestClient

from app.main import app


def test_health():
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "connected"}
