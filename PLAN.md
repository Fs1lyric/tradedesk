# tradedesk build plan

The full system from the spec: news ingestion and scoring, signals and recommendations, a risk-gated
execution service on Alpaca (paper first), trade tracking and metrics, a backtester with walk-forward
and event studies, a FastAPI backend, a React dashboard and an MV3 browser extension.

Testing runs only in GitHub Actions (`.github/workflows/ci.yml`), never locally.

## Layout

```
backend/app/core/                 config, security (JWT, Argon2, envelope encryption)
backend/app/services/nlp/         dedup (SimHash), ticker linking, events, sentiment, impact scoring
backend/app/services/signals/     indicators, composite score, calibration, ranking, sizing
backend/app/services/analytics/   FIFO lot matching, performance metrics
backend/app/services/risk/        pre-trade risk manager
backend/app/services/execution/   order state machine, execution service, reconciliation
backend/app/services/brokers/     adapter protocol, Alpaca (httpx), in-memory fake
backend/app/services/rules/       JSON rule DSL and evaluator
backend/app/services/backtest/    engine, costs, strategies, walk-forward, DSR/PBO, event study, trade comparison
backend/app/models/ + migrations/ SQLAlchemy models and Alembic schema
backend/app/api/                  REST and WebSocket routers
backend/app/workers/              Celery app, tasks, broker stream process
frontend/                         React + TS + Vite dashboard
extension/                        Manifest V3 news capture extension
```

## Phases

| # | Phase | Status |
|---|---|---|
| 0 | Repo, uv project, CI (ruff, mypy, pytest with Postgres+Redis services) | done |
| 1 | Core quant libraries with tests: indicators, news scoring, metrics, lots, risk, state machine, rules, backtester, walk-forward, DSR/PBO, event study, trade comparison | done |
| 2 | Brokers: adapter protocol, Alpaca REST adapter, fake broker; execution service + reconciliation | done (SQL store pending, phase 3) |
| 3 | Database models + Alembic migration of the spec schema; repositories | todo |
| 4 | API: auth, accounts, watchlists, news, recommendations, rules, orders, trades, analytics, backtests, WebSocket | todo |
| 5 | Workers: ingest (RSS, Alpaca News), NLP pipeline, signals, execution, reconcile, snapshots, Beat schedule | todo |
| 6 | Frontend dashboard (8 pages in the spec) with CI screenshot job under Playwright | todo |
| 7 | Browser extension | todo |
| 8 | Live hardening: TOTP-gated live enable, IBKR/Schwab adapters | todo |

## Decisions

- Python 3.12, uv. The sentiment model is pluggable: FinBERT behind the `nlp` extra, with a
  finance lexicon model as the default, so CI doesn't pull torch.
- The Alpaca adapter talks to the REST API with httpx rather than alpaca-py, so it can be tested
  with `httpx.MockTransport`.
- Backtest and live share the same `Strategy`, `RiskManager`, cost model and lot matcher.
- Walk-forward uses seeded random search, not Optuna, to keep dependencies small.

## Next session

Session 1 (2026-10-18) built phases 0-2: every module under `backend/app/services/` with tests,
green in CI. Nothing in `models/`, `api/`, `workers/`, `frontend/` or `extension/` yet.

Start phase 3:
1. `app/core/config.py` (pydantic-settings: DATABASE_URL, REDIS_URL, JWT_SECRET, MASTER_KEY) and
   `app/core/security.py` (Argon2, JWT access/refresh, AES-GCM envelope encryption, TOTP).
2. SQLAlchemy models matching the spec schema. Store embeddings as REAL[] rather than pgvector.
3. Alembic initial migration as explicit SQL. Timescale `create_hypertable` is guarded so plain
   Postgres also works. Add a CI test that runs `alembic upgrade head` and checks model columns
   against the database.
4. `SqlExecutionStore` implementing `ExecutionStore` from `services/execution/service.py`.
Then phases 4-8 in order. Keep committing in small steps.

Notes for whoever continues:
- `Backtester` treats bar.ts as the bar start; `bar_duration` (16h default) moves it to the close.
- `ExecutionService.drain_updates` only works for `FakeBroker`; live uses `run_stream`.
- Alpaca prices are rounded in `price_str` (2 decimals >= $1).
