import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "./api";
import type { Order } from "./types";

export interface Loaded<T> { data: T | undefined; error: string | null; loading: boolean; reload: () => void }

/** Run `load` on mount and whenever `deps` change; `reload` re-runs it. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fn = useCallback(load, deps);
  useEffect(() => {
    let live = true;
    setLoading(true);
    fn().then(
      (d) => { if (live) { setData(d); setError(null); } },
      (e: Error) => { if (live) setError(e.message); },
    ).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [fn, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

/** Live order updates for the signed-in user's accounts; reconnects with backoff. */
export function useOrderStream(onOrder: (o: Order) => void): boolean {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onOrder);
  handler.current = onOrder;
  useEffect(() => {
    let ws: WebSocket | null = null;
    let stop = false;
    let delay = 1000;
    let timer: ReturnType<typeof setTimeout>;
    const open = () => {
      if (stop || typeof WebSocket === "undefined") return;
      ws = new WebSocket(wsUrl("/ws/orders"));
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "subscribed") { setConnected(true); delay = 1000; }
        if (msg.type === "order") handler.current(msg.order as Order);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!stop) { timer = setTimeout(open, delay); delay = Math.min(delay * 2, 30000); }
      };
    };
    open();
    return () => { stop = true; clearTimeout(timer); ws?.close(); };
  }, []);
  return connected;
}
