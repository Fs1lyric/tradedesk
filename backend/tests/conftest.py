import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

DATABASE_URL = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(DATABASE_URL is None, reason="DATABASE_URL not set")


def alembic_config() -> Config:
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL or "")
    return cfg


def run_alembic(cmd: str, target: str) -> None:
    """Run an Alembic command on a worker thread: env.py calls asyncio.run, which can't nest
    inside the event loop of an async test or fixture."""
    with ThreadPoolExecutor(1) as pool:
        pool.submit(getattr(command, cmd), alembic_config(), target).result()


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Rebuild the schema once per session and return the database URL."""
    assert DATABASE_URL is not None
    run_alembic("downgrade", "base")
    run_alembic("upgrade", "head")
    return DATABASE_URL


@pytest.fixture
async def api(migrated_db: str):
    """An httpx client on a fresh app over an emptied database."""
    import base64

    import httpx
    from sqlalchemy import text

    from app.core.config import Settings
    from app.core.events import InMemoryEventBus
    from app.core.locks import LocalAccountLocks
    from app.db import make_engine, make_sessionmaker
    from app.main import create_app

    engine = make_engine(migrated_db)
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE users, symbols, news_sources, news_items, price_bars, signal_snapshots"
            " CASCADE"
        ))
    settings = Settings(jwt_secret="t" * 32, master_key=base64.b64encode(b"k" * 32).decode())
    app = create_app(settings, make_sessionmaker(engine), events=InMemoryEventBus(),
                     locks=LocalAccountLocks())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.app = app  # type: ignore[attr-defined]
        yield client
    await engine.dispose()


async def signup(api, email: str = "a@example.com", password: str = "correct horse 1") -> dict:
    """Register and log in; return auth headers."""
    r = await api.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = await api.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
