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
| 0 | Repo, uv project, CI (ruff, mypy, pytest with Postgres+Redis services) | in progress |
| 1 | Core quant libraries with tests: indicators, news scoring, metrics, lots, risk, state machine, rules, backtester, walk-forward, DSR/PBO, event study, trade comparison | in progress |
| 2 | Brokers: adapter protocol, Alpaca REST adapter, fake broker; execution service + reconciliation | todo |
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

(updated at the end of each session)
