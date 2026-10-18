import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAccounts } from "../account";
import { EquityChart } from "../components/EquityChart";
import { Badge, Empty, ErrorNote, NeedAccount, Panel, Stat, statusKind } from "../components/ui";
import { money, num, pct, tone, when } from "../format";
import { useLoad, useOrderStream } from "../hooks";
import type { Order } from "../types";

export function Dashboard() {
  const { current } = useAccounts();
  const id = current?.id;
  const analytics = useLoad(() => (id ? api.analytics(id) : Promise.resolve(undefined)), [id]);
  const orders = useLoad(() => (id ? api.orders(id) : Promise.resolve([])), [id]);
  const recs = useLoad(() => (id ? api.recommendations(id) : Promise.resolve([])), [id]);
  const [live, setLive] = useState<Order[]>([]);
  const connected = useOrderStream((o) => {
    if (o.account_id !== id) return;
    setLive((prev) => [o, ...prev.filter((p) => p.id !== o.id)].slice(0, 20));
    orders.reload();
  });

  if (!current) return <NeedAccount />;
  const m = analytics.data?.metrics ?? {};
  const curve = analytics.data?.equity ?? [];
  const last = curve[curve.length - 1]?.equity;
  return (
    <div className="grid">
      <Panel title={`${current.label} · ${current.mode}`} actions={current.kill_switch && <Badge kind="bad">kill switch on</Badge>}>
        <div className="stats">
          <Stat label="Equity" value={money(last)} />
          <Stat label="Return" value={pct(m.cumulative_return)} tone={tone(m.cumulative_return)} />
          <Stat label="Sharpe" value={num(m.sharpe)} />
          <Stat label="Max drawdown" value={pct(m.max_drawdown)} tone="down" />
          <Stat label="Win rate" value={pct(m.win_rate, 0)} />
          <Stat label="Trades" value={num(m.n_trades, 0)} />
        </div>
        <ErrorNote error={analytics.error} />
        <EquityChart points={curve} />
      </Panel>
      <Panel title="Pending recommendations" actions={<Link to="/recommendations">Review</Link>}>
        {recs.data?.length ? (
          <ul className="list">{recs.data.slice(0, 5).map((r) => (
            <li key={r.id}><strong>{r.side.toUpperCase()} {r.symbol}</strong>
              <span className="muted">{num(r.qty, 0)} @ {money(r.entry_price)}</span>
              <span className={tone(r.expected_return)}>{pct(r.expected_return)}</span></li>
          ))}</ul>
        ) : <Empty>Nothing waiting for approval.</Empty>}
      </Panel>
      <Panel title="Open orders" actions={<Badge kind={connected ? "ok" : "dim"}>{connected ? "live" : "offline"}</Badge>}>
        {orders.data?.length ? (
          <table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Limit</th><th>Status</th></tr></thead>
            <tbody>{orders.data.map((o) => (
              <tr key={o.id}><td>{o.symbol}</td><td>{o.side}</td><td>{num(o.filled_qty, 0)}/{num(o.qty, 0)}</td>
                <td>{money(o.limit_price)}</td><td><Badge kind={statusKind(o.status)}>{o.status}</Badge></td></tr>
            ))}</tbody></table>
        ) : <Empty>No open orders.</Empty>}
        {live.length > 0 && <>
          <h3>Live updates</h3>
          <ul className="list">{live.map((o) => (
            <li key={o.id}><span>{o.symbol} {o.side}</span><Badge kind={statusKind(o.status)}>{o.status}</Badge>
              <span className="muted">{when(o.updated_at)}</span></li>
          ))}</ul>
        </>}
      </Panel>
    </div>
  );
}
