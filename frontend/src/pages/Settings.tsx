import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useAccounts } from "../account";
import { Badge, Empty, ErrorNote, Panel } from "../components/ui";
import { useLoad } from "../hooks";

export function Settings() {
  return <div className="grid two"><Accounts /><Watchlists /><Security /></div>;
}

function Accounts() {
  const { accounts, reload, select, current } = useAccounts();
  const [form, setForm] = useState({ label: "", api_key: "", api_secret: "", mode: "paper" });
  const [error, setError] = useState<string | null>(null);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const a = await api.createAccount({ broker: "alpaca", ...form });
      setForm({ label: "", api_key: "", api_secret: "", mode: "paper" });
      await reload();
      select(a.id);
    } catch (err) { setError((err as Error).message); }
  };
  const act = (p: Promise<unknown>) => p.then(reload, (e: Error) => setError(e.message));
  return (
    <Panel title="Broker accounts">
      {!accounts.length ? <Empty>No accounts yet. Paper keys from Alpaca are the place to start.</Empty> : (
        <ul className="list">{accounts.map((a) => (
          <li key={a.id}>
            <strong>{a.label}</strong><Badge kind={a.mode === "live" ? "bad" : ""}>{a.mode}</Badge>
            <span className="muted small">{a.broker} {a.api_key_hint}</span>
            {a.kill_switch && <Badge kind="bad">kill switch</Badge>}
            <div className="row">
              {a.id !== current?.id && <button onClick={() => select(a.id)}>Use</button>}
              {a.kill_switch
                ? <button onClick={() => act(api.patchAccount(a.id, { kill_switch: false }))}>Resume trading</button>
                : <button className="danger" onClick={() => confirm("Cancel all orders and close all positions?") && act(api.kill(a.id, true))}>Kill &amp; flatten</button>}
              <button onClick={() => confirm(`Delete ${a.label}?`) && act(api.deleteAccount(a.id))}>Delete</button>
            </div>
          </li>
        ))}</ul>
      )}
      <form className="stack" onSubmit={add}>
        <h3>Add an account</h3>
        <input placeholder="Label" value={form.label} required onChange={(e) => setForm({ ...form, label: e.target.value })} aria-label="Label" />
        <input placeholder="API key id" value={form.api_key} required onChange={(e) => setForm({ ...form, api_key: e.target.value })} aria-label="API key id" />
        <input placeholder="API secret" type="password" value={form.api_secret} required autoComplete="off"
          onChange={(e) => setForm({ ...form, api_secret: e.target.value })} aria-label="API secret" />
        <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })} aria-label="Mode">
          <option value="paper">paper</option><option value="live">live (needs live trading enabled)</option>
        </select>
        <button className="primary">Add account</button>
        <ErrorNote error={error} />
      </form>
    </Panel>
  );
}

function Watchlists() {
  const lists = useLoad(() => api.watchlists(), []);
  const [name, setName] = useState("");
  const [adding, setAdding] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const act = (p: Promise<unknown>) => p.then(lists.reload, (e: Error) => setError(e.message));
  return (
    <Panel title="Watchlists">
      <ErrorNote error={error} />
      {lists.data?.map((w) => (
        <div key={w.id} className="watchlist">
          <div className="row"><strong>{w.name}</strong>
            <button className="link" onClick={() => act(api.deleteWatchlist(w.id))}>delete</button></div>
          <div className="row">{w.symbols.map((s) => (
            <Badge key={s}>{s} <button className="x" aria-label={`Remove ${s}`} onClick={() => act(api.removeSymbol(w.id, s))}>×</button></Badge>
          ))}</div>
          <form className="row" onSubmit={(e) => { e.preventDefault(); act(api.addSymbol(w.id, adding[w.id] ?? "")); setAdding({ ...adding, [w.id]: "" }); }}>
            <input placeholder="Add symbol" value={adding[w.id] ?? ""} aria-label={`Add symbol to ${w.name}`}
              onChange={(e) => setAdding({ ...adding, [w.id]: e.target.value.toUpperCase() })} />
          </form>
        </div>
      ))}
      <form className="row" onSubmit={(e) => { e.preventDefault(); act(api.createWatchlist(name)); setName(""); }}>
        <input placeholder="New watchlist" value={name} required onChange={(e) => setName(e.target.value)} aria-label="New watchlist name" />
        <button>Create</button>
      </form>
    </Panel>
  );
}

function Security() {
  const me = useLoad(() => api.me(), []);
  const [enroll, setEnroll] = useState<{ secret: string; uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [liveCode, setLiveCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const run = (p: Promise<unknown>) => p.then(() => { setError(null); me.reload(); }, (e: Error) => setError(e.message));
  return (
    <Panel title="Security">
      <p>{me.data?.email}</p>
      <p>Two-factor: {me.data?.totp_enabled ? <Badge kind="ok">on</Badge> : <Badge kind="warn">off</Badge>}</p>
      {!me.data?.totp_enabled && (enroll ? (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); run(api.totpConfirm(enroll.secret, code).then(() => setEnroll(null))); }}>
          <p className="small">Add this secret to your authenticator app, then enter the code it shows.</p>
          <code className="secret">{enroll.secret}</code>
          <input inputMode="numeric" placeholder="6-digit code" value={code} onChange={(e) => setCode(e.target.value)} aria-label="TOTP code" />
          <button className="primary">Confirm</button>
        </form>
      ) : <button onClick={() => api.totpEnroll().then(setEnroll, (e: Error) => setError(e.message))}>Set up two-factor</button>)}
      <h3>Live trading</h3>
      {me.data?.live_trading_enabled ? <p><Badge kind="bad">enabled</Badge> Live accounts can place real orders.</p> : (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); run(api.liveEnable(liveCode)); }}>
          <p className="small">Live trading needs two-factor turned on, and a fresh code to switch it on.</p>
          <input inputMode="numeric" placeholder="6-digit code" value={liveCode} disabled={!me.data?.totp_enabled}
            onChange={(e) => setLiveCode(e.target.value)} aria-label="Code to enable live trading" />
          <button disabled={!me.data?.totp_enabled}>Enable live trading</button>
        </form>
      )}
      <ErrorNote error={error} />
    </Panel>
  );
}
