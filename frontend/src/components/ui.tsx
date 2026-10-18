import type { ReactNode } from "react";

export function Panel({ title, actions, children }: { title?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel">
      {(title || actions) && <header className="panel-head"><h2>{title}</h2><div className="row">{actions}</div></header>}
      {children}
    </section>
  );
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: string }) {
  return <div className="stat"><span className="muted">{label}</span><strong className={tone}>{value}</strong></div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

export function Badge({ children, kind = "" }: { children: ReactNode; kind?: string }) {
  return <span className={`badge ${kind}`}>{children}</span>;
}

export function NeedAccount() {
  return <Empty>Add a broker account in Settings to get started.</Empty>;
}

export const statusKind = (s: string) =>
  ({ FILLED: "ok", executed: "ok", done: "ok", CANCELED: "dim", dismissed: "dim", expired: "dim",
     RISK_REJECTED: "bad", BROKER_REJECTED: "bad", rejected: "bad", failed: "bad",
     pending: "warn", queued: "warn", running: "warn" } as Record<string, string>)[s] ?? "";
