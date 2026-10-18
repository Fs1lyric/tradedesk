import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Empty, ErrorNote, Panel } from "../components/ui";
import { signed, tone, when } from "../format";
import type { News } from "../types";

const PAGE = 50;

export function NewsFeed() {
  const [symbol, setSymbol] = useState("");
  const [items, setItems] = useState<News[]>([]);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (before?: number) => {
    try {
      const page = await api.news({ symbol: symbol.trim() || undefined, before, limit: PAGE });
      setItems((prev) => (before ? [...prev, ...page] : page));
      setMore(page.length === PAGE);
      setError(null);
    } catch (e) { setError((e as Error).message); }
  };
  useEffect(() => { load(); }, []);

  return (
    <Panel title="News" actions={
      <form className="row" onSubmit={(e) => { e.preventDefault(); load(); }}>
        <input placeholder="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} aria-label="Filter by symbol" />
        <button>Filter</button>
      </form>}>
      <ErrorNote error={error} />
      {items.length === 0 ? <Empty>No news yet. Sources are polled every two minutes.</Empty> : (
        <ul className="news">{items.map((n) => (
          <li key={n.id}>
            <a href={n.url} target="_blank" rel="noreferrer">{n.title}</a>
            <div className="row muted small">
              <span>{n.source ?? "unknown"}</span><span>{when(n.available_at)}</span>
              <Badge>{n.event_type}</Badge>
              <span className={tone(n.sentiment)}>sentiment {signed(n.sentiment)}</span>
              {n.cluster_id !== null && n.cluster_id !== n.id && <Badge kind="dim">repeat</Badge>}
            </div>
            <div className="row">{n.tickers.map((t) => (
              <Badge key={t.symbol} kind={t.impact > 0 ? "ok" : t.impact < 0 ? "bad" : ""}>
                {t.symbol} {signed(t.impact)}
              </Badge>
            ))}</div>
          </li>
        ))}</ul>
      )}
      {more && <button onClick={() => load(items[items.length - 1].id)}>Load more</button>}
    </Panel>
  );
}
