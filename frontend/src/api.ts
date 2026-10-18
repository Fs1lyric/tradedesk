// Typed client for the backend. The access token lives in memory, the refresh token in
// localStorage; a 401 triggers one refresh-and-retry.
import type {
  Account, Analytics, Backtest, Execution, Me, News, Order, Recommendation, Rule, TokenPair,
  Trade, Watchlist,
} from "./types";

export const BASE = import.meta.env.VITE_API_URL ?? "/api";
const REFRESH_KEY = "tradedesk.refresh";

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

let access: string | null = null;
let refreshing: Promise<boolean> | null = null;
const listeners = new Set<(signedIn: boolean) => void>();

function storage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

export function onAuthChange(fn: (signedIn: boolean) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setTokens(pair: TokenPair | null) {
  access = pair?.access_token ?? null;
  const s = storage();
  if (pair) s?.setItem(REFRESH_KEY, pair.refresh_token);
  else s?.removeItem(REFRESH_KEY);
  listeners.forEach((fn) => fn(pair !== null));
}

export function accessToken(): string | null { return access; }
export function hasSession(): boolean { return access !== null || !!storage()?.getItem(REFRESH_KEY); }

async function refresh(): Promise<boolean> {
  const token = storage()?.getItem(REFRESH_KEY);
  if (!token) return false;
  refreshing ??= (async () => {
    try {
      const r = await fetch(`${BASE}/auth/refresh`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: token }),
      });
      if (!r.ok) { setTokens(null); return false; }
      setTokens(await r.json());
      return true;
    } finally { refreshing = null; }
  })();
  return refreshing;
}

export async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  if (!access && retry && path !== "/auth/login") await refresh();
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (access) headers.Authorization = `Bearer ${access}`;
  const r = await fetch(`${BASE}${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.status === 401 && retry && !path.startsWith("/auth/") && (await refresh())) {
    return request<T>(method, path, body, false);
  }
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new ApiError(r.status, (data as { detail?: unknown }).detail ?? r.statusText);
  }
  return (r.status === 204 ? undefined : await r.json()) as T;
}

const get = <T,>(p: string) => request<T>("GET", p);
const post = <T,>(p: string, b?: unknown) => request<T>("POST", p, b ?? {});

export const api = {
  async login(email: string, password: string, totp_code?: string) {
    setTokens(await request<TokenPair>("POST", "/auth/login", { email, password, totp_code }, false));
  },
  register: (email: string, password: string) => request<Me>("POST", "/auth/register", { email, password }, false),
  async logout() {
    const token = storage()?.getItem(REFRESH_KEY);
    if (token) await request("POST", "/auth/logout", { refresh_token: token }, false).catch(() => {});
    setTokens(null);
  },
  me: () => get<Me>("/auth/me"),
  totpEnroll: () => post<{ secret: string; uri: string }>("/auth/totp/enroll"),
  totpConfirm: (secret: string, code: string) => post<void>("/auth/totp/confirm", { secret, code }),
  liveEnable: (code: string) => post<Me>("/auth/live/enable", { code }),

  accounts: () => get<Account[]>("/accounts"),
  createAccount: (body: Record<string, unknown>) => post<Account>("/accounts", body),
  patchAccount: (id: string, body: Record<string, unknown>) => request<Account>("PATCH", `/accounts/${id}`, body),
  deleteAccount: (id: string) => request<void>("DELETE", `/accounts/${id}`),
  kill: (id: string, flatten: boolean) => post<{ kill_switch: boolean }>(`/accounts/${id}/kill`, { flatten }),
  reconcile: (id: string) => post<{ changed: string[] }>(`/accounts/${id}/reconcile`),

  watchlists: () => get<Watchlist[]>("/watchlists"),
  createWatchlist: (name: string) => post<Watchlist>("/watchlists", { name }),
  deleteWatchlist: (id: string) => request<void>("DELETE", `/watchlists/${id}`),
  addSymbol: (id: string, s: string) => request<void>("PUT", `/watchlists/${id}/symbols/${encodeURIComponent(s)}`),
  removeSymbol: (id: string, s: string) => request<void>("DELETE", `/watchlists/${id}/symbols/${encodeURIComponent(s)}`),

  news: (q: { symbol?: string; before?: number; limit?: number } = {}) => {
    const p = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && v !== "" && p.set(k, String(v)));
    return get<News[]>(`/news${p.size ? `?${p}` : ""}`);
  },

  recommendations: (acct: string, status: "pending" | "all" = "pending") =>
    get<Recommendation[]>(`/accounts/${acct}/recommendations?status=${status}`),
  approve: (id: string) => post<{ recommendation: Recommendation } & Execution>(`/recommendations/${id}/approve`),
  dismiss: (id: string) => post<Recommendation>(`/recommendations/${id}/dismiss`),

  orders: (acct: string, status: "open" | "all" = "open") => get<Order[]>(`/accounts/${acct}/orders?status=${status}`),
  placeOrder: (acct: string, body: Record<string, unknown>) => post<Execution>(`/accounts/${acct}/orders`, body),
  cancelOrder: (id: string) => post<unknown>(`/orders/${id}/cancel`),
  trades: (acct: string) => get<Trade[]>(`/accounts/${acct}/trades`),
  analytics: (acct: string) => get<Analytics>(`/accounts/${acct}/analytics`),

  rules: (acct: string) => get<Rule[]>(`/accounts/${acct}/rules`),
  createRule: (acct: string, def: unknown) => post<Rule>(`/accounts/${acct}/rules`, def),
  patchRule: (id: string, body: Record<string, unknown>) => request<Rule>("PATCH", `/rules/${id}`, body),
  deleteRule: (id: string) => request<void>("DELETE", `/rules/${id}`),

  backtests: () => get<Backtest[]>("/backtests"),
  backtest: (id: string) => get<Backtest>(`/backtests/${id}`),
  queueBacktest: (body: Record<string, unknown>) => post<Backtest>("/backtests", body),
  deleteBacktest: (id: string) => request<void>("DELETE", `/backtests/${id}`),
};

export function wsUrl(path: string): string {
  const base = BASE.startsWith("http") ? BASE.replace(/^http/, "ws")
    : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${BASE}`;
  return `${base}${path}?token=${encodeURIComponent(access ?? "")}`;
}
