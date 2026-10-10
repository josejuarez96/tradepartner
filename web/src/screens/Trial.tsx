import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Replay, ReplayRebalance } from "@/lib/types";
import { shortDate, tone } from "@/lib/format";
import { cn } from "@/lib/utils";

const toneText = { gain: "text-gain", loss: "text-loss", flat: "text-muted-foreground" } as const;
export const pts1 = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}`;
export const yearDate = (iso: string) => `${shortDate(iso)}, ${iso.slice(0, 4)}`;
/** Helpers and figures shared by the workstation view (TrialBench). */
/** A whole-percent return with a true minus sign: "+50%", "0%", "−20%". */
export const p0 = (v: number) => { const n = Math.round(v * 100); return `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n)}%`; };

export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useLayoutEffect(() => {
    const el = ref.current!;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/**
 * How the scores spread that day, and where the cut fell: every stock scored
 * is counted in a bar by its past-year return; bars past the cut are the
 * names held. Labels say which side is which, so there is no legend.
 */
export function SignalPicture({ r, bins, caption = true }: { r: ReplayRebalance; bins: Replay["bins"]; caption?: boolean }) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const H = 140;
  const max = Math.max(...r.hist);
  const bw = W / bins.n;
  const xOf = (v: number) => ((v - bins.lo) / (bins.hi - bins.lo)) * W;
  const cx = xOf(r.cut);
  const ticks = [-0.5, 0, 0.5, 1, 1.5];
  return (
    <figure className={caption ? "mt-7" : "mt-3"}>
      <figcaption className={caption ? "mb-2 text-[13px]" : "sr-only"}>
        Every stock scored, by its past-year return. The engine holds everything to the right of the cut, a return of <span className="num">{p0(r.cut)}</span> or more. <span className="text-muted-foreground">Scores are not stored by the engine yet; these are sample.</span>
      </figcaption>
      <div ref={ref} className="relative" role="img" aria-label={`${r.n_scored} stocks scored; the cut is at ${p0(r.cut)}; ${r.hold} held.`}>
        <svg width={W} height={H} className="block">
          {r.hist.map((c, i) => {
            const x0 = i * bw, held = x0 + bw / 2 >= cx;
            const h = max ? (c / max) * (H - 18) : 0;
            return <rect key={i} x={x0 + 1} y={H - h} width={Math.max(1, bw - 2)} height={h} className={held ? "fill-foreground/75" : "fill-foreground/20"} />;
          })}
          <line x1={cx} x2={cx} y1={0} y2={H} className="stroke-foreground" strokeWidth={1.5} strokeDasharray="3 3" />
        </svg>
        <span className="text-muted-foreground absolute top-0 text-[11px] whitespace-nowrap" style={{ right: Math.max(0, W - cx + 6) }}>not held</span>
        <span className="absolute top-0 text-[11px] whitespace-nowrap" style={{ left: Math.min(W - 70, cx + 6) }}>held: {r.hold}</span>
        <div className="num text-muted-foreground relative mt-1 h-4 text-[11px]">
          {ticks.map((t) => <span key={t} className="absolute -translate-x-1/2" style={{ left: xOf(t) }}>{p0(t)}</span>)}
        </div>
      </div>
    </figure>
  );
}

/** One row per calendar year: the strategy's return minus the S&P's, as a bar from a centre line. */
export function YearBars({ replay }: { replay: Replay }) {
  const rows = useMemo(() => {
    const years = [...new Set(replay.days.map((d) => d.date.slice(0, 4)))];
    return years.map((y) => {
      const ds = replay.days.filter((d) => d.date.slice(0, 4) === y);
      const prev = replay.days[replay.days.indexOf(ds[0]) - 1] ?? ds[0];
      const a = ds.at(-1)!.v / prev.v - 1, b = ds.at(-1)!.b / prev.b - 1;
      const partial = ds[0].date > `${y}-01-05` ? `${y}, from ${shortDate(ds[0].date)}` : y;
      return { y: partial, excess: a - b };
    });
  }, [replay]);
  return <Bars rows={rows.map((x) => ({ label: x.y, v: x.excess }))} />;
}

export function CostBars({ replay }: { replay: Replay }) {
  return (
    <>
      <Bars rows={replay.cost_levels.map((c) => ({ label: `${c.bp} bp a side${c.bp === replay.rule.per_side_bps ? ", assumed" : ""}`, v: c.vs_spy, strong: c.bp === replay.rule.per_side_bps }))} />
      <p className="text-muted-foreground mt-2 max-w-[70ch] text-xs">
        The strategy trades about {Math.round(replay.annual_turnover * 100)}% of its value a year, so every extra basis point of cost takes about {(replay.annual_turnover / 100).toFixed(2)} points a year off the result.
      </p>
    </>
  );
}

/** Rows of signed values as bars from a centre line, each with its number beside it. */
export function Bars({ rows, unit = "" }: { rows: { label: string; v: number; strong?: boolean }[]; unit?: string }) {
  const m = Math.max(...rows.map((r) => Math.abs(r.v)), 0.001);
  return (
    <ul className="border-t">
      {rows.map((r) => {
        const w = (Math.abs(r.v) / m) * 50;
        const t = tone(r.v);
        return (
          <li key={r.label} className={cn("grid min-h-11 grid-cols-[minmax(0,9rem)_1fr_5.5rem] items-center gap-3 border-b text-[13px] sm:grid-cols-[12rem_1fr_6.5rem]", r.strong && "font-medium")}>
            <span className={cn("truncate", !r.strong && "text-muted-foreground")}>{r.label}</span>
            <span className="relative h-3">
              <span className="bg-ring/60 absolute inset-y-[-4px] left-1/2 w-px" aria-hidden />
              <span className={cn("absolute inset-y-0 rounded-sm", t === "loss" ? "bg-loss/70" : "bg-gain/70")} style={r.v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
            </span>
            <span className={cn("num text-right", toneText[t])}>{pts1(r.v)} pts{unit}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function Assumptions({ replay }: { replay: Replay }) {
  const r = replay.rule;
  const rows: [string, string][] = [
    ["Universe", `The ${r.universe.toLocaleString("en-US")} largest US stocks, rebuilt at every rebalance from data as it was known that day; stocks that later delisted are included`],
    ["Signal", `${r.measure.charAt(0).toUpperCase()}${r.measure.slice(1)}`],
    ["Holdings", `The top ${Math.round(r.top_fraction * 100)}% of ranked stocks, equal weight, long only; the rest in cash`],
    ["Rebalance", "The last trading day of each month"],
    ["Fills", `At the ${r.fill_price}; sells first, then buys`],
    ["Costs", `${r.per_side_bps} bp a side on every trade`],
    ["Benchmark", "S&P 500 (SPY), bought once, dividends reinvested"],
  ];
  return (
    <dl className="border-t text-[13px]">
      {rows.map(([k, v]) => (
        <div key={k} className="grid gap-x-6 border-b py-2.5 sm:grid-cols-[9rem_1fr]">
          <dt className="text-muted-foreground">{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
