import os
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


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Rebuild the schema once per session and return the database URL."""
    assert DATABASE_URL is not None
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return DATABASE_URL
