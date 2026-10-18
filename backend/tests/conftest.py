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
