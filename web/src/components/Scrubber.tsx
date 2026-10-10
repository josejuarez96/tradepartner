import { useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { cn } from "@/lib/utils";

interface Props {
  /** The whole run's value, one point per day, drawn as a miniature. */
  values: { date: string; value: number }[];
  /** Rebalance dates, one tick each; the playhead snaps to them. */
  stops: string[];
  at: number;
  onSeek: (i: number) => void;
  label: string;
  className?: string;
}

/**
 * A timeline scrubber: a miniature of the whole run with a tick per
 * rebalance. Drag or click to move the playhead to the nearest rebalance;
 * the part already played is drawn in ink, the rest faint. Arrow keys step,
 * Home and End jump, Page keys move six at a time.
 */
export function Scrubber({ values, stops, at, onSeek, label, className }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(600);
  const dragging = useRef(false);
  useLayoutEffect(() => {
    const el = ref.current!;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const H = 36, pad = 6;
  const n = values.length;
  const lo = Math.min(...values.map((v) => v.value)), hi = Math.max(...values.map((v) => v.value));
  const ix = new Map(values.map((v, i) => [v.date, i]));
  const x = (i: number) => pad + (i / (n - 1)) * (W - 2 * pad);
  const y = (v: number) => 4 + (1 - (v - lo) / (hi - lo || 1)) * (H - 14);
  const stopX = stops.map((d) => x(ix.get(d) ?? 0));
  const headX = at === stops.length - 1 ? x(n - 1) : stopX[at];
  const path = (to: number) => values.slice(0, to + 1).map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v.value).toFixed(1)}`).join("");
  const nearest = (px: number) => stopX.reduce((best, sx, i) => (Math.abs(sx - px) < Math.abs(stopX[best] - px) ? i : best), 0);

  const fromPointer = (e: PointerEvent<HTMLDivElement>) => {
    const r = ref.current!.getBoundingClientRect();
    onSeek(nearest(e.clientX - r.left));
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const last = stops.length - 1;
    const to = { ArrowRight: at + 1, ArrowUp: at + 1, ArrowLeft: at - 1, ArrowDown: at - 1, Home: 0, End: last, PageUp: at + 6, PageDown: at - 6 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    onSeek(Math.max(0, Math.min(last, to)));
  };
  const played = at === stops.length - 1 ? n - 1 : ix.get(stops[at]) ?? 0;

  return (
    <div
      ref={ref}
      role="slider" tabIndex={0} aria-label="Rebalance" aria-valuemin={1} aria-valuemax={stops.length} aria-valuenow={at + 1} aria-valuetext={label}
      onKeyDown={onKey}
      onPointerDown={(e) => { dragging.current = true; e.currentTarget.setPointerCapture(e.pointerId); fromPointer(e); }}
      onPointerMove={(e) => dragging.current && fromPointer(e)}
      onPointerUp={() => { dragging.current = false; }}
      className={cn("focus-visible:ring-ring/50 relative cursor-pointer touch-none rounded-md outline-none select-none focus-visible:ring-2", className)}
      style={{ height: H }}
    >
      <svg width={W} height={H} className="block" aria-hidden>
        <path d={path(n - 1)} fill="none" className="stroke-foreground/20" strokeWidth={1.25} />
        <path d={path(played)} fill="none" className="stroke-foreground/80" strokeWidth={1.25} />
        {stopX.map((sx, i) => (
          <line key={i} x1={sx} x2={sx} y1={H - 7} y2={H - 1} className={i <= at ? "stroke-foreground/70" : "stroke-foreground/25"} strokeWidth={1} />
        ))}
        <line x1={headX} x2={headX} y1={0} y2={H} className="stroke-foreground" strokeWidth={1.5} />
        <circle cx={headX} cy={y(values[played].value)} r={3.5} className="fill-foreground stroke-background" strokeWidth={2} />
      </svg>
    </div>
  );
}
