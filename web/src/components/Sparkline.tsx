import type { Point } from "../lib/types";

interface Props { points: Point[]; width?: number; height?: number }

/**
 * A trend glyph, not a chart: no axes, no hover. Neutral ink on purpose; the
 * number beside it carries the colour. Plain SVG (no library) so a row stays cheap.
 */
export function Sparkline({ points, width = 96, height = 32 }: Props) {
  if (points.length < 2) return null;
  const vs = points.map((p) => p.value);
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const span = hi - lo || 1;
  const pad = 2;
  const xy = vs.map((v, i) => [
    (i / (vs.length - 1)) * width,
    pad + (1 - (v - lo) / span) * (height - pad * 2),
  ]);
  const d = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
  const [lx, ly] = xy.at(-1)!;
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
      <path d={d} fill="none" stroke="var(--ink-3)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r="2.5" fill="var(--ink)" />
    </svg>
  );
}
