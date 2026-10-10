import raw from "../../sample/app-data.json";
import type { AppData, Point, PortfolioPoint } from "./types";

export const sample = raw as AppData;

export type RangeKey = "1W" | "1M" | "3M" | "ALL";
export const RANGES: { key: RangeKey; label: string; long: string }[] = [
  { key: "1W", label: "1W", long: "the past week" },
  { key: "1M", label: "1M", long: "the past month" },
  { key: "3M", label: "3M", long: "the past 3 months" },
  { key: "ALL", label: "All", long: "since you started" },
];

/** First date included in a range, counted back from the last data date. */
export function rangeStart(last: string, key: RangeKey): string | null {
  if (key === "ALL") return null;
  const d = new Date(last + "T00:00:00Z");
  if (key === "1W") d.setUTCDate(d.getUTCDate() - 7);
  else d.setUTCMonth(d.getUTCMonth() - (key === "1M" ? 1 : 3));
  return d.toISOString().slice(0, 10);
}

/** Both lines rebased to 0% at the first shared date: the honest comparison. */
export function comparison(own: { date: string; v: number }[], bench: { date: string; close: number }[], from: string | null) {
  const b = new Map(bench.map((p) => [p.date, p.close]));
  const pts = own.filter((p) => b.has(p.date) && (!from || p.date >= from));
  if (pts.length === 0) return { you: [] as Point[], spy: [] as Point[], youRet: 0, spyRet: 0 };
  const y0 = pts[0].v, s0 = b.get(pts[0].date)!;
  const you = pts.map((p) => ({ date: p.date, value: p.v / y0 - 1 }));
  const spy = pts.map((p) => ({ date: p.date, value: b.get(p.date)! / s0 - 1 }));
  return { you, spy, youRet: you.at(-1)!.value, spyRet: spy.at(-1)!.value };
}

/** Change on the last session versus the one before. Money added that day is not a gain. */
export function lastChange(series: PortfolioPoint[]) {
  if (series.length < 2) return null;
  const a = series.at(-2)!, b = series.at(-1)!;
  const rel = b.index / a.index - 1;
  return { abs: a.value * rel, rel };
}
