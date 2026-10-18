import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useAccounts } from "../account";
import { Badge, Empty, ErrorNote, NeedAccount, Panel, statusKind } from "../components/ui";
import { money, num, pct, tone, when } from "../format";
import { useLoad, useOrderStream } from "../hooks";
import type { Execution } from "../types";

export function Orders() {
  const { current } = useAccounts();
  const id = current?.id;
  const [show, setShow] = useState<"open" | "all">("open");
  const orders = useLoad(() => (id ? api.orders(id, show) : Promise.resolve([])), [id, show]);
  const trades = useLoad(() => (id ? api.trades(id) : Promise.resolve([])), [id]);
  useOrderStream((o) => { if (o.account_id === id) { orders.reload(); trades.reload(); } });
  const [form, setForm] = useState({ symbol: "", side: "buy", qty: "", order_type: "limit", limit_price: "" });
  const [result, setResult] = useState<Execution | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!current) return <NeedAccount />;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const r = await api.placeOrder(current.id, {
        symbol: form.symbol, side: form.side, qty: Number(form.qty), order_type: form.order_type,
        limit_price: form.order_type === "limit" && form.limit_price ? Number(form.limit_price) : undefined,
        idempotency_key: crypto.randomUUID(),
      });
      setResult(r);
      orders.reload();
    } catch (err) { setError((err as Error).message); }
  };
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="grid">
      <Panel title="New order">
        <form className="order-form" onSubmit={submit}>
          <input placeholder="Symbol" value={form.symbol} required aria-label="Symbol"
            onChange={(e) => setForm({ ...form, symbol: e.target.value.toUpperCase() })} />
          <select value={form.side} onChange={set("side")} aria-label="Side">
            <option>buy</option><option>sell</option><option>short</option><option>cover</option>
          </select>
          <input type="number" min="0" step="any" placeholder="Qty" value={form.qty} required onChange={set("qty")} aria-label="Quantity" />
          <select value={form.order_type} onChange={set("order_type")} aria-label="Order type">
            <option value="limit">limit</option><option value="market">market</option>
          </select>
          {form.order_type === "limit" && <input type="number" step="any" placeholder="Limit (blank: from quote)"
            value={form.limit_price} onChange={set("limit_price")} aria-label="Limit price" />}
          <button className="primary" disabled={current.kill_switch}>Place order</button>
        </form>
        <ErrorNote error={error} />
        {result && (result.order
          ? <p className="ok">Sent: {result.order.side} {result.order.qty} {result.order.symbol} ({result.order.status})</p>
          : <p className="error">Blocked by risk: {result.risk.checks.filter((c) => !c.passed).map((c) => `${c.name}${c.detail ? ` (${c.detail})` : ""}`).join(", ")}</p>)}
      </Panel>
      <Panel title="Orders" actions={<>
        <select value={show} onChange={(e) => setShow(e.target.value as "open" | "all")} aria-label="Show orders">
          <option value="open">Open</option><option value="all">All</option></select>
        <button onClick={() => api.reconcile(current.id).then(orders.reload)}>Reconcile</button></>}>
        {!orders.data?.length ? <Empty>No orders.</Empty> : (
          <table><thead><tr><th>Submitted</th><th>Symbol</th><th>Side</th><th>Filled</th><th>Type</th><th>Price</th><th>Status</th><th /></tr></thead>
            <tbody>{orders.data.map((o) => (
              <tr key={o.id}><td>{when(o.submitted_at)}</td><td>{o.symbol}</td><td>{o.side}</td>
                <td>{num(o.filled_qty, 0)}/{num(o.qty, 0)}</td><td>{o.order_type} {o.order_class !== "simple" && o.order_class}</td>
                <td>{money(o.filled_avg_price ?? o.limit_price)}</td><td><Badge kind={statusKind(o.status)}>{o.status}</Badge></td>
                <td>{["ACCEPTED", "SUBMITTED", "PARTIALLY_FILLED"].includes(o.status) &&
                  <button onClick={() => api.cancelOrder(o.id).then(orders.reload, (e) => setError(e.message))}>Cancel</button>}</td></tr>
            ))}</tbody></table>
        )}
      </Panel>
      <Panel title="Trades">
        {!trades.data?.length ? <Empty>No trades yet.</Empty> : (
          <table><thead><tr><th>Symbol</th><th>Dir</th><th>Qty</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Return</th><th>Opened</th></tr></thead>
            <tbody>{trades.data.map((t, i) => (
              <tr key={i}><td>{t.symbol}</td><td>{t.direction}</td><td>{num(t.qty, 0)}</td><td>{money(t.entry_price)}</td>
                <td>{t.closed ? money(t.exit_price) : <Badge kind="warn">open</Badge>}</td>
                <td className={tone(t.realized_pnl)}>{money(t.realized_pnl)}</td>
                <td className={tone(t.return_pct)}>{pct(t.return_pct)}</td><td>{when(t.entry_at)}</td></tr>
            ))}</tbody></table>
        )}
      </Panel>
    </div>
  );
}
