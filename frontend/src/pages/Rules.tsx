import { useState } from "react";
import { api } from "../api";
import { useAccounts } from "../account";
import { Badge, Empty, ErrorNote, NeedAccount, Panel } from "../components/ui";
import { when } from "../format";
import { useLoad } from "../hooks";

const TEMPLATE = {
  name: "Positive news on an uptrend",
  universe: { symbols: ["AAPL", "MSFT"] },
  when: { all: [
    { feature: "signal.trend", op: ">", value: 0.2 },
    { feature: "news.sentiment", op: ">", value: 0.3 },
  ] },
  action: { side: "buy", sizing: "atr_risk", risk_per_trade: 0.005, order: "bracket", stop_atr: 2, target_atr: 3 },
  cooldown_minutes: 240,
  mode: "approve",
};

export function Rules() {
  const { current } = useAccounts();
  const id = current?.id;
  const rules = useLoad(() => (id ? api.rules(id) : Promise.resolve([])), [id]);
  const [draft, setDraft] = useState(JSON.stringify(TEMPLATE, null, 2));
  const [error, setError] = useState<string | null>(null);

  if (!current) return <NeedAccount />;
  const create = async () => {
    setError(null);
    let def: unknown;
    try { def = JSON.parse(draft); } catch { setError("The rule isn't valid JSON."); return; }
    try { await api.createRule(current.id, def); rules.reload(); }
    catch (e) { setError((e as Error).message === "HTTP 422" ? "The rule doesn't match the rule schema." : (e as Error).message); }
  };
  return (
    <div className="grid two">
      <Panel title="Rules">
        <ErrorNote error={rules.error} />
        {!rules.data?.length ? <Empty>No rules yet.</Empty> : (
          <ul className="list">{rules.data.map((r) => (
            <li key={r.id}>
              <strong>{r.name}</strong><Badge kind={r.mode === "auto" ? "warn" : ""}>{r.mode}</Badge>
              <span className="muted small">{when(r.created_at)}</span>
              <div className="row">
                <label className="toggle"><input type="checkbox" checked={r.enabled}
                  onChange={() => api.patchRule(r.id, { enabled: !r.enabled }).then(rules.reload)} /> enabled</label>
                <button onClick={() => setDraft(JSON.stringify(r.definition, null, 2))}>Copy</button>
                <button className="danger" onClick={() => api.deleteRule(r.id).then(rules.reload)}>Delete</button>
              </div>
            </li>
          ))}</ul>
        )}
      </Panel>
      <Panel title="New rule" actions={<button className="primary" onClick={create}>Create</button>}>
        <p className="muted small">Conditions can read <code>signal.*</code> (trend, momentum, rsi, breakout, volume, direction_score,
          expected_return) and <code>news.*</code> (sentiment, attention_z, count_24h). Modes: approve, manual, auto.</p>
        <textarea className="code" rows={22} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Rule JSON" spellCheck={false} />
        <ErrorNote error={error} />
      </Panel>
    </div>
  );
}
