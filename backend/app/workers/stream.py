"""Long-running process: one broker trade-update stream per account, applied to the order
store and published to the event bus. Run with `python -m app.workers.stream`."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.core.config import get_settings
from app.models import BrokerAccount
from app.runtime import Runtime
from app.workers.jobs.accounts import all_accounts

log = logging.getLogger(__name__)


async def _stream(rt: Runtime, acct: BrokerAccount) -> None:
    async with rt.execution(acct, lock=False) as svc:
        await svc.run_stream()


async def stream_all(rt: Runtime, refresh_s: float = 60.0) -> None:
    """Keep a stream task per account, starting and stopping them as accounts come and go."""
    tasks: dict[str, asyncio.Task[None]] = {}
    try:
        while True:
            current = {a.id: a for a in await all_accounts(rt.sessions)}
            for acct_id in list(tasks):
                if acct_id not in current or tasks[acct_id].done():
                    tasks.pop(acct_id).cancel()
            for acct_id, acct in current.items():
                if acct_id not in tasks:
                    tasks[acct_id] = asyncio.create_task(_stream(rt, acct))
            await asyncio.sleep(refresh_s)
    finally:
        for t in tasks.values():
            t.cancel()
        for t in tasks.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    rt = Runtime.from_settings(get_settings())
    try:
        await stream_all(rt)
    finally:
        await rt.aclose()


if __name__ == "__main__":
    asyncio.run(main())
