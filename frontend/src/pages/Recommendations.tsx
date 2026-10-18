import { useState } from "react";
import { api } from "../api";
import { useAccounts } from "../account";
import { Badge, Empty, ErrorNote, NeedAccount, Panel, statusKind } from "../components/ui";
import { money, num, pct, tone, when } from "../format";
import { useLoad } from "../hooks";
import type { Risk } from "../types";

export function Recommendations() {
  const { current } = useAccounts();
  const id = current?.id;
  const [show, setShow] = useState<"pending" | "all">("pending");
  const recs = useLoad(() => (id ? api.recommendations(id, show) : Promise.resolve([])), [id, show]);
  const [outcome, setOutcome] = useState<Record<string, Risk>>({});
  const [error, setError] = useState<string | null>(null);

  if (!current) return <NeedAccount />;
  const act = async (recId: string, approve: boolean) => {
    setError(null);
    try {
      if (approve) {
        const r = await api.approve(recId);
        setOutcome((o) => ({ ...o, [recId]: r.risk }));
      } else await api.dismiss(recId);
      recs.reload();
    } catch (e) { setError((e as Error).message); }
  };

  return (
    <Panel title="Recommendations" actions={
      <select value={show} onChange={(e) => setShow(e.target.value as "pending" | "all")} aria-label="Show">
        <option value="pending">Pending</option><option value="all">History</option>
      </select>}>
      <ErrorNote error={error || recs.error} />
      {Object.entries(outcome).map(([recId, risk]) => !risk.passed && (
        <p key={recId} className="error">Blocked by risk: {risk.checks.filter((c) => !c.passed).map((c) => c.name).join(", ")}</p>
      ))}
      {!recs.data?.length ? <Empty>No recommendations. Rules run every five minutes.</Empty> : (
        <table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Stop / target</th>
          <th>Exp. return</th><th>Confidence</th><th>Created</th><th /></tr></thead>
          <tbody>{recs.data.map((r) => (
            <tr key={r.id}>
              <td><strong>{r.symbol}</strong></td><td>{r.side}</td><td>{num(r.qty, 0)}</td><td>{money(r.entry_price)}</td>
              <td>{money(r.stop_loss)} / {money(r.take_profit)}</td>
              <td className={tone(r.expected_return)}>{pct(r.expected_return)}</td>
              <td>{pct(r.confidence, 0)}</td><td>{when(r.created_at)}</td>
              <td>{r.status === "pending" ? (
                <div className="row"><button className="primary" onClick={() => act(r.id, true)}>Approve</button>
                  <button onClick={() => act(r.id, false)}>Dismiss</button></div>
              ) : <Badge kind={statusKind(r.status)}>{r.status}</Badge>}</td>
            </tr>
          ))}</tbody></table>
      )}
    </Panel>
  );
}
