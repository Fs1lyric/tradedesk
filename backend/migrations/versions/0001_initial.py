"""Initial schema.

Explicit SQL rather than autogenerate, so the DDL is reviewable. The Timescale steps only run
when the extension is available; plain Postgres gets ordinary tables.

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = """
CREATE TABLE users (
    id uuid PRIMARY KEY,
    email text NOT NULL,
    password_hash text NOT NULL,
    totp_secret_enc bytea,
    live_trading_enabled boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE TABLE refresh_tokens (
    jti text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

CREATE TABLE broker_accounts (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    broker text NOT NULL,
    mode text NOT NULL DEFAULT 'paper' CHECK (mode IN ('paper', 'live')),
    label text NOT NULL,
    api_key_enc bytea NOT NULL,
    api_secret_enc bytea NOT NULL,
    risk_limits jsonb NOT NULL DEFAULT '{}',
    kill_switch boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_broker_accounts_user_id ON broker_accounts (user_id);

CREATE TABLE audit_log (
    id bigserial PRIMARY KEY,
    user_id uuid REFERENCES users (id) ON DELETE SET NULL,
    action text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}',
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_log_user_id ON audit_log (user_id);

CREATE TABLE symbols (
    symbol text PRIMARY KEY,
    name text NOT NULL,
    exchange text,
    aliases text[] NOT NULL DEFAULT '{}',
    tradable boolean NOT NULL DEFAULT true,
    shortable boolean NOT NULL DEFAULT false
);

CREATE TABLE price_bars (
    symbol text NOT NULL,
    timeframe text NOT NULL,
    ts timestamptz NOT NULL,
    open double precision NOT NULL,
    high double precision NOT NULL,
    low double precision NOT NULL,
    close double precision NOT NULL,
    volume double precision NOT NULL,
    vwap double precision,
    PRIMARY KEY (symbol, timeframe, ts)
);

CREATE TABLE watchlists (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_watchlists_user_id ON watchlists (user_id);

CREATE TABLE watchlist_symbols (
    watchlist_id uuid NOT NULL REFERENCES watchlists (id) ON DELETE CASCADE,
    symbol text NOT NULL,
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (watchlist_id, symbol)
);

CREATE TABLE news_sources (
    id serial PRIMARY KEY,
    name text NOT NULL,
    kind text NOT NULL,
    url text,
    credibility double precision NOT NULL DEFAULT 0.5,
    social boolean NOT NULL DEFAULT false,
    enabled boolean NOT NULL DEFAULT true,
    CONSTRAINT uq_news_sources_name UNIQUE (name)
);

CREATE TABLE news_items (
    id bigserial PRIMARY KEY,
    source_id integer REFERENCES news_sources (id) ON DELETE SET NULL,
    url text NOT NULL,
    url_canonical text NOT NULL,
    title text NOT NULL,
    body text NOT NULL DEFAULT '',
    published_at timestamptz,
    available_at timestamptz NOT NULL,
    simhash bigint NOT NULL,
    cluster_id bigint,
    event_type text NOT NULL,
    event_mult double precision NOT NULL,
    sentiment double precision NOT NULL,
    novelty double precision NOT NULL,
    embedding real[],
    CONSTRAINT uq_news_items_url_canonical UNIQUE (url_canonical)
);
CREATE INDEX ix_news_items_available_at ON news_items (available_at);
CREATE INDEX ix_news_items_cluster_id ON news_items (cluster_id);

CREATE TABLE news_tickers (
    news_id bigint NOT NULL REFERENCES news_items (id) ON DELETE CASCADE,
    symbol text NOT NULL,
    confidence double precision NOT NULL,
    relevance double precision NOT NULL,
    novelty double precision NOT NULL,
    impact double precision NOT NULL,
    PRIMARY KEY (news_id, symbol)
);
CREATE INDEX ix_news_tickers_symbol ON news_tickers (symbol);

CREATE TABLE signal_snapshots (
    symbol text NOT NULL,
    ts timestamptz NOT NULL,
    features jsonb NOT NULL,
    direction_score double precision NOT NULL,
    expected_return double precision NOT NULL,
    support integer NOT NULL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE rules (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES broker_accounts (id) ON DELETE CASCADE,
    name text NOT NULL,
    definition jsonb NOT NULL,
    mode text NOT NULL DEFAULT 'approve' CHECK (mode IN ('manual', 'approve', 'auto')),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_rules_account_id ON rules (account_id);

CREATE TABLE recommendations (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES broker_accounts (id) ON DELETE CASCADE,
    rule_id uuid REFERENCES rules (id) ON DELETE SET NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    qty double precision NOT NULL,
    entry_price double precision NOT NULL,
    stop_loss double precision,
    take_profit double precision,
    expected_return double precision NOT NULL,
    score double precision NOT NULL,
    confidence double precision NOT NULL,
    rationale jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz
);
CREATE INDEX ix_recommendations_account_id ON recommendations (account_id);
CREATE INDEX ix_recommendations_status ON recommendations (status);

CREATE TABLE trade_intents (
    id bigserial PRIMARY KEY,
    key text NOT NULL,
    account_id uuid NOT NULL REFERENCES broker_accounts (id) ON DELETE CASCADE,
    symbol text NOT NULL,
    side text NOT NULL,
    qty double precision NOT NULL,
    order_type text NOT NULL,
    limit_price double precision,
    stop_loss double precision,
    take_profit double precision,
    time_in_force text NOT NULL,
    extended_hours boolean NOT NULL,
    origin text NOT NULL,
    rule_id text,
    recommendation_id text,
    cooldown_minutes integer,
    risk jsonb NOT NULL,
    passed boolean NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE INDEX ix_trade_intents_account_created ON trade_intents (account_id, created_at);

CREATE TABLE orders (
    id uuid PRIMARY KEY,
    intent_key text NOT NULL,
    account_id uuid NOT NULL REFERENCES broker_accounts (id) ON DELETE CASCADE,
    client_order_id text NOT NULL,
    broker_order_id text,
    symbol text NOT NULL,
    side text NOT NULL,
    qty double precision NOT NULL,
    order_type text NOT NULL,
    order_class text NOT NULL,
    limit_price double precision,
    stop_loss double precision,
    take_profit double precision,
    time_in_force text NOT NULL,
    status text NOT NULL,
    filled_qty double precision NOT NULL DEFAULT 0,
    filled_avg_price double precision,
    submitted_at timestamptz,
    updated_at timestamptz,
    CONSTRAINT uq_orders_client_order_id UNIQUE (client_order_id),
    CONSTRAINT uq_orders_broker_order_id UNIQUE (broker_order_id)
);
CREATE INDEX ix_orders_account_status ON orders (account_id, status);
CREATE INDEX ix_orders_submitted_at ON orders (submitted_at);

CREATE TABLE fills (
    id bigserial PRIMARY KEY,
    order_id uuid NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    account_id uuid NOT NULL REFERENCES broker_accounts (id) ON DELETE CASCADE,
    broker_fill_id text NOT NULL,
    symbol text NOT NULL,
    qty double precision NOT NULL,
    price double precision NOT NULL,
    fee double precision NOT NULL DEFAULT 0,
    filled_at timestamptz NOT NULL,
    CONSTRAINT uq_fills_broker_fill_id UNIQUE (broker_fill_id)
);
CREATE INDEX ix_fills_account_filled ON fills (account_id, filled_at);

CREATE TABLE cooldowns (
    account_id uuid NOT NULL REFERENCES broker_accounts (id) ON DELETE CASCADE,
    rule_id text NOT NULL,
    symbol text NOT NULL,
    until timestamptz NOT NULL,
    PRIMARY KEY (account_id, rule_id, symbol)
);

CREATE TABLE trading_halts (
    account_id uuid NOT NULL REFERENCES broker_accounts (id) ON DELETE CASCADE,
    day date NOT NULL,
    halted_at timestamptz NOT NULL,
    PRIMARY KEY (account_id, day)
);

CREATE TABLE equity_snapshots (
    account_id uuid NOT NULL REFERENCES broker_accounts (id) ON DELETE CASCADE,
    ts timestamptz NOT NULL,
    equity double precision NOT NULL,
    cash double precision NOT NULL,
    buying_power double precision NOT NULL,
    PRIMARY KEY (account_id, ts)
);

CREATE TABLE backtest_runs (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    kind text NOT NULL,
    strategy text NOT NULL,
    params jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    metrics jsonb,
    result jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX ix_backtest_runs_user_id ON backtest_runs (user_id);
"""

HYPERTABLES = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
        PERFORM create_hypertable('price_bars', 'ts', if_not_exists => TRUE);
        PERFORM create_hypertable('equity_snapshots', 'ts', if_not_exists => TRUE);
    END IF;
END
$$;
"""

DROP_ORDER = [
    "backtest_runs", "equity_snapshots", "trading_halts", "cooldowns", "fills", "orders",
    "trade_intents", "recommendations", "rules", "signal_snapshots", "news_tickers",
    "news_items", "news_sources", "watchlist_symbols", "watchlists", "price_bars", "symbols",
    "audit_log", "broker_accounts", "refresh_tokens", "users",
]


def upgrade() -> None:
    # asyncpg prepares each statement, and a prepared statement holds one command.
    for stmt in TABLES.split(";"):
        if stmt.strip():
            op.execute(stmt)
    op.execute(HYPERTABLES)


def downgrade() -> None:
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
