import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="craftboard-test-")
os.environ["CRAFTBOARD_DB"] = os.path.join(_tmp, "test.db")
os.environ["CRAFTBOARD_UPLOADS"] = os.path.join(_tmp, "uploads")
os.environ["DEFAULT_PLAN"] = "pro"
os.environ["ALLOW_SIMULATED_PAYMENTS"] = "true"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("STRIPE_SECRET_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


_counter = {"n": 0}


@pytest.fixture()
def creator(client):
    """A signed-in Pro creator with two commission types."""
    _counter["n"] += 1
    n = _counter["n"]
    r = client.post("/api/auth/signup", json={"email": f"artist{n}@example.com", "password": "correct-horse",
                                              "name": f"Artist {n}", "handle": f"artist{n}"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    client.cookies.clear()
    chibi = client.post("/api/types", json={"name": "Chibi portrait", "base_price": 60, "est_hours": 2}, headers=h).json()
    sheet = client.post("/api/types", json={"name": "Character sheet", "base_price": 150, "est_hours": 7}, headers=h).json()
    return {"handle": f"artist{n}", "headers": h, "types": {"chibi": chibi, "sheet": sheet}}
