// Canned API responses so the dashboard renders realistic data without a backend.
const now = Date.now();
const iso = (minsAgo: number) => new Date(now - minsAgo * 60_000).toISOString();
const ACCT = "11111111-1111-4111-8111-111111111111";

const equity = Array.from({ length: 120 }, (_, i) => {
  const e = 100_000 * (1 + 0.0012 * i + 0.012 * Math.sin(i / 6));
  return { ts: iso((120 - i) * 1440), equity: Math.round(e * 100) / 100 };
});
let peak = 0;
const curve = equity.map((p) => { peak = Math.max(peak, p.equity); return { ...p, drawdown: p.equity / peak - 1 }; });

const order = (n: number, status: string, extra: object = {}) => ({
  id: `o${n}`, account_id: ACCT, client_order_id: `c${n}`, broker_order_id: `b${n}`, symbol: ["AAPL", "NVDA", "MSFT"][n % 3],
  side: n % 2 ? "sell" : "buy", qty: 10 * n, order_type: "limit", order_class: n === 1 ? "bracket" : "simple",
  limit_price: 180 + n, stop_loss: null, take_profit: null, time_in_force: "day", status, filled_qty: status === "FILLED" ? 10 * n : 0,
  filled_avg_price: status === "FILLED" ? 181 + n : null, submitted_at: iso(30 * n), updated_at: iso(20 * n), ...extra,
});

export const routes: Record<string, unknown> = {
  "/auth/me": { id: "u1", email: "trader@example.com", totp_enabled: true, live_trading_enabled: false },
  "/accounts": [{ id: ACCT, broker: "alpaca", mode: "paper", label: "Paper desk", api_key_hint: "…7Q2X",
    risk_limits: { max_daily_loss_pct: 0.02, max_position_pct: 0.1 }, kill_switch: false, created_at: iso(50000) }],
  [`/accounts/${ACCT}/analytics`]: { metrics: { cumulative_return: 0.153, sharpe: 1.42, max_drawdown: -0.061, win_rate: 0.58, n_trades: 47 }, equity: curve },
  [`/accounts/${ACCT}/orders`]: [order(1, "ACCEPTED"), order(2, "PARTIALLY_FILLED", { filled_qty: 8 })],
  [`/accounts/${ACCT}/orders?status=all`]: [order(1, "ACCEPTED"), order(2, "PARTIALLY_FILLED"), order(3, "FILLED"), order(4, "CANCELED")],
  [`/accounts/${ACCT}/recommendations`]: [
    { id: "r1", account_id: ACCT, rule_id: "x", symbol: "NVDA", side: "buy", qty: 24, entry_price: 131.4, stop_loss: 124.9,
      take_profit: 141.2, expected_return: 0.021, score: 1.8, confidence: 0.74, rationale: {}, status: "pending", created_at: iso(6), expires_at: iso(-24) },
    { id: "r2", account_id: ACCT, rule_id: "x", symbol: "TSLA", side: "short", qty: 12, entry_price: 242.1, stop_loss: 255.3,
      take_profit: 222.3, expected_return: -0.018, score: 1.2, confidence: 0.41, rationale: {}, status: "pending", created_at: iso(14), expires_at: iso(-16) },
  ],
  [`/accounts/${ACCT}/trades`]: [
    { symbol: "AAPL", direction: "long", qty: 40, entry_at: iso(9000), entry_price: 172.3, exit_qty: 40, exit_price: 181.9, exit_at: iso(7000), fees: 0, realized_pnl: 384, return_pct: 0.0557, closed: true },
    { symbol: "AMD", direction: "short", qty: 30, entry_at: iso(6000), entry_price: 158.2, exit_qty: 30, exit_price: 162.4, exit_at: iso(5000), fees: 0, realized_pnl: -126, return_pct: -0.0265, closed: true },
    { symbol: "NVDA", direction: "long", qty: 20, entry_at: iso(300), entry_price: 128.8, exit_qty: 0, exit_price: null, exit_at: null, fees: 0, realized_pnl: 0, return_pct: 0, closed: false },
  ],
  [`/accounts/${ACCT}/rules`]: [
    { id: "ru1", account_id: ACCT, name: "Positive news on an uptrend", mode: "approve", enabled: true, definition: { name: "Positive news on an uptrend" }, created_at: iso(20000) },
    { id: "ru2", account_id: ACCT, name: "Fade negative overreaction", mode: "auto", enabled: false, definition: { name: "Fade" }, created_at: iso(10000) },
  ],
  "/news?limit=50": [
    { id: 3, title: "Nvidia (NVDA) raises guidance as data-center demand accelerates", url: "#", source: "wire", published_at: iso(9), available_at: iso(8),
      event_type: "guidance", sentiment: 0.71, novelty: 0.9, cluster_id: 3, tickers: [{ symbol: "NVDA", confidence: 0.95, relevance: 1, novelty: 0.9, impact: 0.62 }] },
    { id: 2, title: "Tesla recalls 120,000 vehicles over steering fault", url: "#", source: "alpaca", published_at: iso(40), available_at: iso(38),
      event_type: "recall", sentiment: -0.55, novelty: 0.8, cluster_id: 2, tickers: [{ symbol: "TSLA", confidence: 0.9, relevance: 1, novelty: 0.8, impact: -0.48 }] },
    { id: 1, title: "Apple and Microsoft extend cloud partnership", url: "#", source: "wire", published_at: iso(90), available_at: iso(88),
      event_type: "partnership", sentiment: 0.22, novelty: 0.6, cluster_id: 1, tickers: [
        { symbol: "AAPL", confidence: 0.85, relevance: 1, novelty: 0.6, impact: 0.14 }, { symbol: "MSFT", confidence: 0.85, relevance: 1, novelty: 0.6, impact: 0.12 }] },
  ],
  "/watchlists": [{ id: "w1", name: "Megacap tech", symbols: ["AAPL", "AMZN", "MSFT", "NVDA"] }],
  "/backtests": [
    { id: "bt1", kind: "backtest", strategy: "composite", params: {}, status: "done", metrics: { cumulative_return: 0.184, sharpe: 1.21, sortino: 1.77, max_drawdown: -0.094, win_rate: 0.55, n_trades: 132 }, error: null, created_at: iso(120), finished_at: iso(118) },
    { id: "bt2", kind: "walkforward", strategy: "news_momentum", params: {}, status: "running", metrics: null, error: null, created_at: iso(5), finished_at: null },
    { id: "bt3", kind: "event_study", strategy: "composite", params: {}, status: "failed", metrics: null, error: "ValueError: no price bars for ZZZZ", created_at: iso(300), finished_at: iso(299) },
  ],
};
routes["/backtests/bt1"] = { ...(routes["/backtests"] as object[])[0], result: { equity: curve.map((p) => [p.ts, p.equity]) } };
