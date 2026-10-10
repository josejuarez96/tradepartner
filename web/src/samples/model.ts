import { sample } from "@/lib/data";
import { comparison, lastChange, rangeStart, type RangeKey } from "@/lib/data";

/** The same numbers for every sample, so only the design differs. */
export function model(range: RangeKey = "ALL") {
  const d = sample;
  const port = d.portfolio.equity;
  const own = port.map((p) => ({ date: p.date, v: p.index }));
  const last = port.at(-1)!;
  const cmp = comparison(own, d.benchmark.closes, rangeStart(last.date, range));
  const today = lastChange(port)!;
  const books = d.books.map((b) => {
    const c = comparison(b.equity.map((p) => ({ date: p.date, v: p.value })), d.benchmark.closes, null);
    return {
      id: b.id, name: b.name, strategy: d.strategies.find((s) => s.id === b.strategy_id)!.name,
      value: b.equity.at(-1)!.value, ret: c.youRet, gap: c.youRet - c.spyRet, young: b.equity.length < 5,
      next: `${b.next_run.what}, ${new Date(b.next_run.at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}`,
      spark: b.equity.map((p) => p.value),
    };
  });
  return { total: last.value, today, cmp, start: own[0].date, books, waiting: d.research.waiting.slice(0, 3) };
}

/** An SVG path for a series scaled into a w×h box (shared y-range across series). */
export function paths(series: number[][], w: number, h: number, pad = 4) {
  const all = series.flat();
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const y = (v: number) => pad + (1 - (v - lo) / span) * (h - pad * 2);
  const out = series.map((s) => s.map((v, i) => `${i ? "L" : "M"}${((i / (s.length - 1)) * w).toFixed(1)} ${y(v).toFixed(1)}`).join(""));
  return { lines: out, zero: y(0) };
}
