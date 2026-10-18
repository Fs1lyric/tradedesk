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
backend/app/models/ + migrations/ SQLAlchemy models and Alembic schema (explicit SQL)
backend/app/db.py                 async engine and session factory
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
| 2 | Brokers: adapter protocol, Alpaca REST adapter, fake broker; execution service + reconciliation | done |
| 3 | Settings + security; database models + Alembic migration; SQL execution store | done |
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

Session 1 (2026-10-18) built phases 0-2. Session 2 (2026-10-18) built phase 3, green in CI
(144 tests):
- `core/config.py` (Settings: DATABASE_URL, REDIS_URL, JWT_SECRET >= 32 chars, MASTER_KEY =
  base64 of 32 bytes) and `core/security.py` (Argon2id, HS256 access/refresh JWTs with `type`
  and `jti`, AES-GCM envelope encryption, TOTP with a +-1 step window).
- 22 tables in `app/models/`, created by `migrations/versions/0001_initial.py`. `price_bars` and
  `equity_snapshots` become hypertables only when timescaledb is available.
  `tests/test_migrations.py` checks that model and DB columns and nullability match both ways.
- `SqlExecutionStore` in `services/execution/sql_store.py`. `tests/test_execution_store.py` is
  one contract suite that runs against both the in-memory and the SQL store.

Start phase 4 (API). Suggested order: `app/main.py` + dependencies (settings, session, current
user from the access token); auth (register, login, refresh with jti rotation via
`refresh_tokens`, TOTP enrol/verify); broker accounts (keys stored with `encrypt_secret`);
watchlists; news; recommendations (approve -> TradeIntent -> ExecutionService); rules;
orders/trades (FIFO lots via `analytics.lots`); analytics; backtests (queue a `backtest_runs`
row); WebSocket for order updates. Use httpx `AsyncClient` + `ASGITransport` for the tests.

Notes for whoever continues:
- `Backtester` treats bar.ts as the bar start; `bar_duration` (16h default) moves it to the close.
- `ExecutionService.drain_updates` only works for `FakeBroker`; live uses `run_stream`.
- Alpaca prices are rounded in `price_str` (2 decimals >= $1).
- No spec file is in the repo; the schema came from the service dataclasses. If the original
  spec turns up, diff it against `0001_initial.py`.
- Ids are UUIDs in Postgres but `str` in Python (`Uuid(as_uuid=False)`), to match the services.
- Each execution store call is its own transaction; the per-account Redis lock (phase 5)
  is what makes `execute` safe from races.
- Trading halts are keyed by the UTC date of `now`. Switch this to the exchange (ET) date when
  the market calendar lands.
- DB tests need DATABASE_URL and skip without it. Alembic runs on a worker thread in tests
  (`tests/conftest.py::run_alembic`) because env.py calls asyncio.run.
