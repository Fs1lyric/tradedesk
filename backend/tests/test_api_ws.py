"""The WebSocket needs Starlette's TestClient, which runs the app on its own event loop, so
this test builds its own app and engine instead of using the `api` fixture."""

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.core.events import InMemoryEventBus
from app.db import make_engine, make_sessionmaker
from app.main import create_app
from tests.conftest import requires_db

pytestmark = requires_db

ACCOUNT = {"broker": "alpaca", "label": "paper", "api_key": "PK123456", "api_secret": "s"}


@pytest.fixture
def client(migrated_db: str):
    engine = make_engine(migrated_db)
    settings = Settings(jwt_secret="t" * 32, master_key=base64.b64encode(b"k" * 32).decode())
    bus = InMemoryEventBus()
    app = create_app(settings, make_sessionmaker(engine), events=bus)

    async def truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE users CASCADE"))

    with TestClient(app) as c:
        c.portal.call(truncate)
        yield c, bus
        c.portal.call(engine.dispose)


def _token(c: TestClient, email: str) -> str:
    creds = {"email": email, "password": "correct horse 1"}
    assert c.post("/auth/register", json=creds).status_code == 201
    return c.post("/auth/login", json=creds).json()["access_token"]


def test_order_updates_reach_only_the_owner(client):
    c, bus = client
    alice = _token(c, "a@example.com")
    bob = _token(c, "b@example.com")
    acct = c.post("/accounts", json=ACCOUNT,
                  headers={"Authorization": f"Bearer {alice}"}).json()["id"]
    c.post("/accounts", json=ACCOUNT, headers={"Authorization": f"Bearer {bob}"})

    with c.websocket_connect(f"/ws/orders?token={alice}") as ws:
        assert ws.receive_json() == {"type": "subscribed", "accounts": [acct]}
        c.portal.call(bus.publish, "orders:someone-else", {"type": "order", "id": "x"})
        c.portal.call(bus.publish, f"orders:{acct}", {"type": "order", "id": "o1"})
        assert ws.receive_json() == {"type": "order", "id": "o1"}


@pytest.mark.parametrize("query", ["", "?token=junk"])
def test_ws_rejects_bad_token(client, query):
    c, _ = client
    with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect(f"/ws/orders{query}") as ws:
        ws.receive_json()
    assert e.value.code == 4401
