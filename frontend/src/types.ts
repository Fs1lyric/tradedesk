// Shapes returned by the backend API (backend/app/api).

export interface Me { id: string; email: string; totp_enabled: boolean; live_trading_enabled: boolean }
export interface TokenPair { access_token: string; refresh_token: string }

export type RiskLimits = Record<string, number>;
export interface Account {
  id: string; broker: string; mode: "paper" | "live"; label: string; api_key_hint: string;
  risk_limits: RiskLimits; kill_switch: boolean; created_at: string;
}
export interface Watchlist { id: string; name: string; symbols: string[] }

export interface Ticker { symbol: string; confidence: number; relevance: number; novelty: number; impact: number }
export interface News {
  id: number; title: string; url: string; source: string | null; published_at: string | null;
  available_at: string; event_type: string; sentiment: number; novelty: number;
  cluster_id: number | null; tickers: Ticker[];
}

export interface Recommendation {
  id: string; account_id: string; rule_id: string | null; symbol: string; side: string; qty: number;
  entry_price: number; stop_loss: number | null; take_profit: number | null; expected_return: number;
  score: number; confidence: number; rationale: Record<string, unknown>; status: string;
  created_at: string; expires_at: string | null;
}

export interface Order {
  id: string; account_id: string; client_order_id: string; broker_order_id: string | null;
  symbol: string; side: string; qty: number; order_type: string; order_class: string;
  limit_price: number | null; stop_loss: number | null; take_profit: number | null;
  time_in_force: string; status: string; filled_qty: number; filled_avg_price: number | null;
  submitted_at: string | null; updated_at: string | null;
}
export interface RiskCheck { name: string; passed: boolean; detail: string }
export interface Risk { passed: boolean; halt_account: boolean; checks: RiskCheck[] }
export interface Execution { risk: Risk; order: Order | null }

export interface Trade {
  symbol: string; direction: string; qty: number; entry_at: string; entry_price: number;
  exit_qty: number; exit_price: number | null; exit_at: string | null; fees: number;
  realized_pnl: number; return_pct: number; closed: boolean;
}
export interface Analytics {
  metrics: Record<string, number | null>;
  equity: { ts: string; equity: number; drawdown: number }[];
}

export interface Rule {
  id: string; account_id: string; name: string; mode: string; enabled: boolean;
  definition: Record<string, unknown>; created_at: string;
}

export interface Backtest {
  id: string; kind: string; strategy: string; params: Record<string, unknown>; status: string;
  metrics: Record<string, number | null> | null; error: string | null; created_at: string;
  finished_at: string | null; result?: Record<string, unknown> | null;
}
