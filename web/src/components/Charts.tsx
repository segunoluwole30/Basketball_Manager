import { useState } from "react";

// Plain-SVG charts. Colours come from CSS classes (styles.css), so they follow
// the light/dark theme; every chart has a text label for screen readers.

export interface Point {
  x: number;          // season
  y: number | null;   // null = no value that season (skipped)
  partial?: boolean;  // season in progress: drawn hollow
}

const W = 240, H = 112, L = 30, R = 10, T = 12, B = 22;

/** One series over seasons: a small multiple (one measure per chart, no dual axes). */
export function SeasonLine({ title, points, format = (v) => v.toFixed(1), zero = true, unit = "" }: {
  title: string; points: Point[]; format?: (v: number) => string; zero?: boolean; unit?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const pts = points.filter((p) => p.y != null) as (Point & { y: number })[];
  if (!pts.length) return null;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = zero ? 0 : Math.min(...ys), y1 = Math.max(...ys);
  if (!zero) { const pad = Math.max(2, (y1 - y0) * 0.15); y0 -= pad; y1 += pad; }
  if (y1 - y0 < 1e-9) y1 = y0 + 1;
  y1 += zero ? (y1 - y0) * 0.1 : 0;
  const sx = (x: number) => (x1 === x0 ? L + (W - L - R) / 2 : L + ((x - x0) / (x1 - x0)) * (W - L - R));
  const sy = (y: number) => T + (1 - (y - y0) / (y1 - y0)) * (H - T - B);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
  const label = `${title} by season: ` + pts.map((p) => `${p.x}${p.partial ? " (so far)" : ""} ${format(p.y)}${unit}`).join(", ");
  const ticks = xs.length <= 6 ? xs : [x0, xs[Math.floor(xs.length / 2)], x1];
  const h = hover != null ? pts[hover] : null;
  return (
    <figure className="chart">
      <figcaption className="chart-title">{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} onMouseLeave={() => setHover(null)}>
        <line className="chart-grid" x1={L} x2={W - R} y1={sy(y0)} y2={sy(y0)} />
        <line className="chart-grid" x1={L} x2={W - R} y1={sy(y1)} y2={sy(y1)} />
        <text className="chart-axis" x={L - 4} y={sy(y1) + 3} textAnchor="end">{format(y1)}</text>
        <text className="chart-axis" x={L - 4} y={sy(y0) + 3} textAnchor="end">{format(y0)}</text>
        {ticks.map((x) => (
          <text key={x} className="chart-axis" x={sx(x)} y={H - 6} textAnchor="middle">{`'${String(x % 100).padStart(2, "0")}`}</text>
        ))}
        {pts.length > 1 && <path className="chart-line" d={path} />}
        {h && <line className="chart-cross" x1={sx(h.x)} x2={sx(h.x)} y1={T} y2={H - B} />}
        {pts.map((p, i) => (
          <circle key={p.x} className={`chart-dot ${p.partial ? "chart-dot-partial" : ""}`} cx={sx(p.x)} cy={sy(p.y)} r={4} />
        ))}
        {pts.map((p, i) => (
          <rect key={`hit-${p.x}`} className="chart-hit" x={sx(p.x) - 14} y={T} width={28} height={H - T - B}
            onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} tabIndex={0}
            aria-label={`${p.x}${p.partial ? " so far" : ""}: ${format(p.y)}${unit}`}>
            <title>{`${p.x}-${String((p.x + 1) % 100).padStart(2, "0")}${p.partial ? " (so far)" : ""}: ${format(p.y)}${unit}`}</title>
          </rect>
        ))}
      </svg>
      <div className="chart-readout small" aria-hidden>
        {h ? <><b>{format(h.y)}{unit}</b> <span className="muted">{h.x}-{String((h.x + 1) % 100).padStart(2, "0")}{h.partial ? " so far" : ""}</span></>
          : <><b>{format(pts[pts.length - 1].y)}{unit}</b> <span className="muted">latest{pts[pts.length - 1].partial ? " (so far)" : ""}</span></>}
      </div>
    </figure>
  );
}
