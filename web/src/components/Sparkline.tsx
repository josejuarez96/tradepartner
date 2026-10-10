import type { Point } from "../lib/types";

interface Props { points: Point[]; tone?: "gain" | "loss" | "flat"; width?: number; height?: number }

/**
 * A trend glyph, not a chart: no axes, no hover. It takes the gain or loss
 * colour of the return beside it, so the row reads at a glance. Plain SVG.
 */
export function Sparkline({ points, tone = "flat", width = 96, height = 28 }: Props) {
  if (points.length < 2) return null;
  const vs = points.map((p) => p.value);
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const span = hi - lo || 1;
  const pad = 3;
  const xy = vs.map((v, i) => [
    (i / (vs.length - 1)) * (width - pad),
    pad + (1 - (v - lo) / span) * (height - pad * 2),
  ]);
  const d = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
  const [lx, ly] = xy.at(-1)!;
  const color = tone === "flat" ? "var(--ink-2)" : `var(--${tone})`;
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r="2.25" fill={color} />
    </svg>
  );
}
