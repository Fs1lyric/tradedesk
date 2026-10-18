import asyncio

from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from tests.conftest import alembic_config, requires_db

pytestmark = requires_db


def _db_schema(url: str) -> dict[str, dict[str, bool]]:
    async def run() -> dict[str, dict[str, bool]]:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.connect() as conn:
            def read(sync_conn):
                insp = inspect(sync_conn)
                return {
                    t: {c["name"]: c["nullable"] for c in insp.get_columns(t)}
                    for t in insp.get_table_names()
                }
            out = await conn.run_sync(read)
        await engine.dispose()
        return out

    return asyncio.run(run())


def _scalar(url: str, sql: str):
    async def run():
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.connect() as conn:
            val = (await conn.execute(text(sql))).scalar()
        await engine.dispose()
        return val

    return asyncio.run(run())


def test_every_model_column_exists_with_matching_nullability(migrated_db):
    db = _db_schema(migrated_db)
    for table in Base.metadata.sorted_tables:
        assert table.name in db, f"missing table {table.name}"
        for col in table.columns:
            assert col.name in db[table.name], f"missing column {table.name}.{col.name}"
            assert db[table.name][col.name] == col.nullable, f"nullability {table.name}.{col.name}"


def test_database_has_no_columns_the_models_lack(migrated_db):
    db = _db_schema(migrated_db)
    for table in Base.metadata.sorted_tables:
        extra = set(db[table.name]) - {c.name for c in table.columns}
        assert not extra, f"{table.name} has unmapped columns {extra}"


def test_time_series_tables_are_hypertables_when_timescale_exists(migrated_db):
    has_ts = _scalar(migrated_db, "select count(*) from pg_extension where extname='timescaledb'")
    if not has_ts:
        return
    names = _scalar(
        migrated_db,
        "select string_agg(hypertable_name, ',' order by hypertable_name)"
        " from timescaledb_information.hypertables",
    )
    assert set(names.split(",")) >= {"equity_snapshots", "price_bars"}


def test_downgrade_then_upgrade_round_trips(migrated_db):
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    assert "orders" not in _db_schema(migrated_db)
    command.upgrade(cfg, "head")
    assert "orders" in _db_schema(migrated_db)
