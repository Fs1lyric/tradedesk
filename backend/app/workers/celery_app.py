"""Celery app and Beat schedule. Times are UTC; 21:xx is after the 16:00 ET close."""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

app = Celery("tradedesk", broker=REDIS_URL, include=["app.workers.tasks"])
app.conf.update(
    timezone="UTC",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    # Backtests are slow; keep them off the queue that places orders.
    task_routes={"app.workers.tasks.run_backtests": {"queue": "backtests"}},
)

WEEKDAYS = "mon-fri"
T = "app.workers.tasks."
app.conf.beat_schedule = {
    "ingest-news": {"task": T + "ingest_news", "schedule": 120.0},
    "refresh-signals": {"task": T + "refresh_signals", "schedule": 300.0},
    "evaluate-rules": {"task": T + "evaluate_rules", "schedule": 300.0},
    "reconcile": {"task": T + "reconcile_orders", "schedule": 60.0},
    "expire-recommendations": {"task": T + "expire_recommendations", "schedule": 60.0},
    "run-backtests": {"task": T + "run_backtests", "schedule": 15.0},
    "sync-bars": {"task": T + "sync_daily_bars",
                  "schedule": crontab(minute=15, hour=21, day_of_week=WEEKDAYS)},
    # Daily, so analytics' annualization (252 periods) matches the snapshot frequency.
    "snapshot-equity": {"task": T + "snapshot_equity",
                        "schedule": crontab(minute=5, hour=21, day_of_week=WEEKDAYS)},
}
