// Equity line with a drawdown band underneath, drawn as plain SVG.
interface Point { ts: string; equity: number; drawdown?: number }

export function EquityChart({ points, height = 220 }: { points: Point[]; height?: number }) {
  if (points.length < 2) return <p className="empty">Not enough history to chart yet.</p>;
  const W = 800, H = height, pad = 8, ddH = H * 0.22, lineH = H - ddH - pad * 3;
  const eq = points.map((p) => p.equity);
  const lo = Math.min(...eq), hi = Math.max(...eq), span = hi - lo || 1;
  const x = (i: number) => pad + (i / (points.length - 1)) * (W - pad * 2);
  const y = (v: number) => pad + lineH - ((v - lo) / span) * lineH;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join("");
  const dds = points.map((p) => p.drawdown ?? 0);
  const ddMin = Math.min(...dds, -1e-9);
  const ddTop = H - pad - ddH;
  const dd = points.map((_, i) => `L${x(i).toFixed(1)},${(ddTop + (dds[i] / ddMin) * ddH).toFixed(1)}`).join("");
  const up = eq[eq.length - 1] >= eq[0];
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
         aria-label={`Equity from ${eq[0].toFixed(0)} to ${eq[eq.length - 1].toFixed(0)}`}>
      <path d={line} className={up ? "eq up" : "eq down"} fill="none" vectorEffect="non-scaling-stroke" />
      <path d={`M${x(0)},${ddTop}${dd}L${x(points.length - 1)},${ddTop}Z`} className="dd" />
      <line x1={pad} x2={W - pad} y1={ddTop} y2={ddTop} className="axis" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
