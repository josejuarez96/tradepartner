import { useEffect, useRef } from "react";
import {
  ColorType, CrosshairMode, LineSeries, LineStyle, createChart, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type ISeriesMarkersPluginApi, type Time,
} from "lightweight-charts";
import type { Point } from "@/lib/types";
import { onThemeChange, token } from "@/lib/tokens";

interface Props {
  /** Both lines over the whole run, rebased to 0%. */
  you: Point[];
  spy: Point[];
  /** Draw only up to this date; the rest of the axis stays in place, empty. */
  until: string;
  /** Rebalance dates, drawn as small dots on the strategy line up to `until`. */
  marks: string[];
  /** The rebalance in focus, drawn larger. */
  current?: string;
  tone: "gain" | "loss" | "flat";
  label: string;
  height?: number;
}

const pctLabel = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(0)}%`;
const dayLabel = (t: Time) =>
  new Date(`${String(t)}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

/**
 * A backtest drawn forward in time: the strategy and the S&P up to the
 * playhead, the rest of the time axis kept empty so nothing rescales while it
 * plays. A transparent series carries the full range for the price scale.
 */
export function ReplayChart({ you, spy, until, marks, current, tone, label, height = 300 }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const s = useRef<{ you: ISeriesApi<"Line">; spy: ISeriesApi<"Line">; scale: ISeriesApi<"Line">; marks: ISeriesMarkersPluginApi<Time> } | null>(null);
  const toneRef = useRef(tone);
  toneRef.current = tone;
  const paintRef = useRef<() => void>(() => {});

  useEffect(() => {
    const c = createChart(host.current!, {
      autoSize: true, handleScroll: false, handleScale: false,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, attributionLogo: false, fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
      crosshair: { mode: CrosshairMode.Magnet, horzLine: { visible: false, labelVisible: false } },
      localization: { priceFormatter: pctLabel, timeFormatter: dayLabel },
    });
    const common = { priceLineVisible: false, lastValueVisible: false } as const;
    const scale = c.addSeries(LineSeries, { ...common, color: "transparent", crosshairMarkerVisible: false, lineWidth: 1 });
    const spyS = c.addSeries(LineSeries, { ...common, lineWidth: 1, lineStyle: LineStyle.Dashed, crosshairMarkerRadius: 3 });
    const youS = c.addSeries(LineSeries, { ...common, lineWidth: 2, crosshairMarkerRadius: 4, crosshairMarkerBorderWidth: 2 });
    s.current = { you: youS, spy: spyS, scale, marks: createSeriesMarkers(youS, []) };
    const paint = () => {
      const t = toneRef.current;
      const line = token(t === "loss" ? "loss" : t === "flat" ? "muted-foreground" : "gain");
      const bg = token("background");
      c.applyOptions({
        layout: { textColor: token("muted-foreground"), fontFamily: "IBM Plex Mono, ui-monospace, monospace" },
        crosshair: { vertLine: { color: token("ring"), width: 1, style: LineStyle.Solid, labelBackgroundColor: token("secondary") } },
      });
      spyS.applyOptions({ color: token("bench"), crosshairMarkerBackgroundColor: token("bench"), crosshairMarkerBorderColor: bg });
      youS.applyOptions({ color: line, crosshairMarkerBackgroundColor: line, crosshairMarkerBorderColor: bg });
    };
    paintRef.current = paint;
    paint();
    const off = onThemeChange(paint);
    chart.current = c;
    return () => { off(); c.remove(); chart.current = null; s.current = null; };
  }, []);

  useEffect(() => { paintRef.current(); }, [tone]);

  // The full range, once: both lines, so the scale never moves while playing.
  useEffect(() => {
    const x = s.current;
    if (!x) return;
    x.scale.setData(you.map((p, i) => ({ time: p.date as Time, value: i % 2 ? p.value : spy[i]?.value ?? p.value })));
    chart.current?.timeScale().fitContent();
  }, [you, spy]);

  useEffect(() => {
    const x = s.current;
    if (!x) return;
    const cut = (pts: Point[]) => pts.map((p) => (p.date <= until ? { time: p.date as Time, value: p.value } : { time: p.date as Time }));
    x.you.setData(cut(you));
    x.spy.setData(cut(spy));
    const fg = token("foreground");
    x.marks.setMarkers(marks.filter((d) => d <= until).map((d) => ({
      time: d as Time, position: "inBar" as const, shape: "circle" as const, size: d === current ? 1.1 : 0.35, color: fg,
    })));
  }, [you, spy, until, marks, current]);

  return <div ref={host} role="img" aria-label={label} className="w-full" style={{ height }} />;
}
