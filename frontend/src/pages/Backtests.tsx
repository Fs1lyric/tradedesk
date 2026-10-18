import { useState, type FormEvent } from "react";
import { api } from "../api";
import { EquityChart } from "../components/EquityChart";
import { Badge, Empty, ErrorNote, Panel, Stat, statusKind } from "../components/ui";
import { num, pct, tone, when } from "../format";
import { useLoad } from "../hooks";

const STRATEGIES = ["composite", "news_momentum", "news_fade", "technical_trend", "buy_and_hold"];
const KINDS = ["backtest", "walkforward", "event_study", "compare"];

export function Backtests() {
  const runs = useLoad(() => api.backtests(), []);
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useLoad(() => (selected ? api.backtest(selected) : Promise.resolve(undefined)), [selected]);
  const [form, setForm] = useState({ kind: "backtest", strategy: "composite", symbols: "AAPL,MSFT", start: "2024-01-01", end: "2025-01-01" });
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const run = await api.queueBacktest({ ...form, symbols: form.symbols.split(/[\s,]+/).filter(Boolean) });
      runs.reload();
      setSelected(run.id);
    } catch (err) { setError((err as Error).message); }
  };
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const d = detail.data;
  const equity = (d?.result?.equity as [string, number][] | undefined)?.map(([ts, equity]) => ({ ts, equity })) ?? [];

  return (
    <div className="grid two">
      <Panel title="Run a backtest">
        <form className="order-form" onSubmit={submit}>
          <select value={form.kind} onChange={set("kind")} aria-label="Kind">{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
          <select value={form.strategy} onChange={set("strategy")} aria-label="Strategy">{STRATEGIES.map((s) => <option key={s}>{s}</option>)}</select>
          <input value={form.symbols} onChange={set("symbols")} aria-label="Symbols" />
          <input type="date" value={form.start} onChange={set("start")} aria-label="Start" />
          <input type="date" value={form.end} onChange={set("end")} aria-label="End" />
          <button className="primary">Queue</button>
        </form>
        <ErrorNote error={error} />
        {!runs.data?.length ? <Empty>No runs yet.</Empty> : (
          <table><thead><tr><th>Created</th><th>Kind</th><th>Strategy</th><th>Sharpe</th><th>Status</th></tr></thead>
            <tbody>{runs.data.map((r) => (
              <tr key={r.id} className={r.id === selected ? "selected" : ""} onClick={() => setSelected(r.id)}>
                <td>{when(r.created_at)}</td><td>{r.kind}</td><td>{r.strategy}</td><td>{num(r.metrics?.sharpe)}</td>
                <td><Badge kind={statusKind(r.status)}>{r.status}</Badge></td></tr>
            ))}</tbody></table>
        )}
        <button onClick={runs.reload}>Refresh</button>
      </Panel>
      <Panel title={d ? `${d.strategy} · ${d.kind}` : "Result"}>
        {!d ? <Empty>Pick a run to see its result.</Empty> : d.status === "failed" ? <p className="error">{d.error}</p> :
          d.status !== "done" ? <Empty>{d.status}…</Empty> : <>
            <div className="stats">
              {d.metrics && Object.entries(d.metrics).filter(([, v]) => typeof v === "number" || v === null).slice(0, 8).map(([k, v]) => (
                <Stat key={k} label={k.replace(/_/g, " ")}
                  value={/return|drawdown|rate|cagr|volatility/.test(k) ? pct(v as number | null) : num(v as number | null)}
                  tone={/return|alpha/.test(k) ? tone(v as number) : undefined} />
              ))}
            </div>
            <EquityChart points={equity} />
          </>}
      </Panel>
    </div>
  );
}
