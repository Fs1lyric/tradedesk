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
backend/app/api/                  REST and WebSocket routers (app factory in app/main.py)
backend/app/core/events.py        EventBus: Redis pub/sub, in-memory for tests
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
| 4 | API: auth, accounts, watchlists, news, recommendations, rules, orders, trades, analytics, backtests, WebSocket | done |
| 5 | Workers: ingest (RSS, Alpaca News), NLP pipeline, signals, execution, reconcile, snapshots, Beat schedule | done |
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

Sessions 1-2 (2026-10-18) built phases 0-3. Session 3 (2026-10-18) built phase 4, green in CI
(179 tests). Endpoints, all behind a bearer access token except register/login/refresh:
- `/auth`: register, login (needs `totp_code` once TOTP is on), refresh (rotates the jti;
  replaying a spent token revokes all of the user's tokens), logout, me, totp/enroll +
  totp/confirm (the secret is only stored after a valid code).
- `/accounts` CRUD (keys envelope-encrypted, only `api_key_hint` comes back; live mode needs
  `users.live_trading_enabled`), `/watchlists` (+ PUT/DELETE `/{id}/symbols/{sym}`).
- `/accounts/{id}/rules` + `/rules/{id}` (definition is `dsl.Rule`; a watchlist universe must be
  the user's). `/news` feed with `symbol`, `before` (keyset on available_at, id), `limit`.
- `/accounts/{id}/recommendations` + `/recommendations/{id}/approve|dismiss`. Approve is
  one-shot (intent key `rec:{id}`): `executed`, `rejected` (risk or broker) or `expired`.
- `/accounts/{id}/orders` (manual intent, key `manual:{acct}:{idempotency_key}`; risk rejection
  is a 200 with `order: null`), `/orders/{id}/cancel` (202; status lands via stream or
  reconcile), `/accounts/{id}/reconcile`, `/accounts/{id}/kill` (`flatten` closes positions),
  `/accounts/{id}/trades` (FIFO from fills), `/accounts/{id}/analytics` (metrics + equity and
  drawdown curve; NaN metrics become null).
- `/backtests`: POST queues a `backtest_runs` row (202) after checking the strategy builds with
  `strategy_params`; GET list/detail, DELETE. Nothing runs it yet.
- `/ws/orders?token=<access>`: sends `{"type":"subscribed","accounts":[...]}`, then forwards
  every event on `orders:{account_id}` for the user's accounts (fixed at connect time).

Start phase 5 (workers). Contracts the API already relies on:
- The stream worker must publish order events to `order_channel(account_id)` via
  `RedisEventBus`; the WebSocket only forwards, it never reads the DB.
- The backtest worker picks up `backtest_runs` with status `queued` (use
  `SELECT ... FOR UPDATE SKIP LOCKED`), sets running/done/failed, `metrics`, `result`,
  `finished_at`. `params` holds symbols, start, end, strategy_params, options.
- The signals worker inserts `recommendations` rows (status `pending`, optional `expires_at`).
- Equity snapshots feed `/analytics`; the snapshot task writes `equity_snapshots`.
- Add the per-account Redis lock around `ExecutionService.execute` and use it from both the
  API (`app/api/trading.py::execution_service`) and the rule worker.

Notes for whoever continues:
- `app.state.broker_factory(account, settings)` builds the adapter; tests swap in a
  `FakeBroker`. Set `fill_immediately = False` on it when the test drives fills itself.
- httpx `ASGITransport` doesn't run lifespan, so app state is built in `create_app`.
  The WebSocket test uses Starlette's `TestClient` with its own app and engine
  (`tests/test_api_ws.py`), because it runs the app on a separate loop.
- `PATCH /accounts/{id}` with `kill_switch` only flips the flag; `/kill` also cancels orders.
- `Backtester` treats bar.ts as the bar start; `bar_duration` (16h default) moves it to the close.
- `ExecutionService.drain_updates` only works for `FakeBroker`; live uses `run_stream`.
- Alpaca prices are rounded in `price_str` (2 decimals >= $1).
- No spec file is in the repo; the schema came from the service dataclasses.
- Ids are UUIDs in Postgres but `str` in Python (`Uuid(as_uuid=False)`); endpoints return 404
  for malformed ids and for other users' ids alike.
- Each execution store call is its own transaction.
- Trading halts are keyed by the UTC date of `now`. Switch this to the exchange (ET) date when
  the market calendar lands.
- DB tests need DATABASE_URL and skip without it. Alembic runs on a worker thread in tests
  (`tests/conftest.py::run_alembic`) because env.py calls asyncio.run.
