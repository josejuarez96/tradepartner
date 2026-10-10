import { useEffect, useRef } from "react";
import {
  AreaSeries, ColorType, CrosshairMode, LineSeries, LineStyle, createChart, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type ISeriesMarkersPluginApi, type MouseEventParams, type Time,
} from "lightweight-charts";
import type { Point } from "@/lib/types";
import { onThemeChange, token } from "@/lib/tokens";

export interface ChartEvent { date: string; label: string }

interface Props {
  you: Point[];
  spy: Point[];
  /** The backtest's expected range around the S&P, drawn as a grey band. */
  band?: { hi: Point[]; lo: Point[] };
  /** Rebalances and book starts, drawn as small dots on your line. */
  events?: ChartEvent[];
  tone?: "gain" | "loss" | "flat";
  label: string;
  onScrub?: (date: string | null) => void;
  height?: number;
}

const pctLabel = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`;
const dayLabel = (t: Time) =>
  new Date(`${String(t)}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
const pts = (s: Point[]) => s.map((p) => ({ time: p.date as Time, value: p.value }));

/**
 * Direction E's hero chart: your line (gain or loss colour), the S&P as a
 * dashed grey line, the backtest's expected range as a band, event dots.
 * No grid, no axis borders; the price scale is quiet.
 */
export function ReturnChart({ you, spy, band, events = [], tone = "gain", label, onScrub, height = 300 }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const s = useRef<{ hi: ISeriesApi<"Area">; lo: ISeriesApi<"Area">; spy: ISeriesApi<"Line">; you: ISeriesApi<"Line">; marks: ISeriesMarkersPluginApi<Time> } | null>(null);
  const toneRef = useRef(tone);
  toneRef.current = tone;
  const paintRef = useRef<() => void>(() => {});
  const scrub = useRef(onScrub);
  scrub.current = onScrub;

  useEffect(() => {
    const c = createChart(host.current!, {
      autoSize: true,
      handleScroll: false,
      handleScale: false,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, attributionLogo: false, fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
      crosshair: { mode: CrosshairMode.Magnet, horzLine: { visible: false, labelVisible: false } },
      localization: { priceFormatter: pctLabel, timeFormatter: dayLabel },
    });
    const common = { priceLineVisible: false, lastValueVisible: false } as const;
    // The band: an area down from the upper edge, then an area in the page colour from the lower edge.
    const hi = c.addSeries(AreaSeries, { ...common, lineVisible: false, crosshairMarkerVisible: false });
    const lo = c.addSeries(AreaSeries, { ...common, lineVisible: false, crosshairMarkerVisible: false });
    const spyS = c.addSeries(LineSeries, { ...common, lineWidth: 1, lineStyle: LineStyle.Dashed, crosshairMarkerRadius: 3 });
    const youS = c.addSeries(LineSeries, { ...common, lineWidth: 2, crosshairMarkerRadius: 4, crosshairMarkerBorderWidth: 2 });
    const marks = createSeriesMarkers(youS, []);
    s.current = { hi, lo, spy: spyS, you: youS, marks };

    const paint = () => {
      const t = toneRef.current;
      const line = token(t === "loss" ? "loss" : t === "flat" ? "muted-foreground" : "gain");
      const bg = token("background");
      c.applyOptions({
        layout: { textColor: token("muted-foreground"), fontFamily: "IBM Plex Mono, ui-monospace, monospace" },
        crosshair: { vertLine: { color: token("ring"), width: 1, style: LineStyle.Solid, labelBackgroundColor: token("secondary") } },
      });
      hi.applyOptions({ topColor: token("foreground", 0.07), bottomColor: token("foreground", 0.07) });
      lo.applyOptions({ topColor: bg, bottomColor: bg });
      spyS.applyOptions({ color: token("bench"), crosshairMarkerBackgroundColor: token("bench"), crosshairMarkerBorderColor: bg });
      youS.applyOptions({ color: line, crosshairMarkerBackgroundColor: line, crosshairMarkerBorderColor: bg });
    };
    paintRef.current = paint;
    paint();
    const off = onThemeChange(paint);
    const move = (p: MouseEventParams<Time>) => scrub.current?.(p.time ? String(p.time) : null);
    c.subscribeCrosshairMove(move);
    chart.current = c;
    return () => { off(); c.unsubscribeCrosshairMove(move); c.remove(); chart.current = null; s.current = null; };
  }, []);

  useEffect(() => { paintRef.current(); }, [tone]);

  useEffect(() => {
    const x = s.current;
    if (!x) return;
    x.hi.setData(band ? pts(band.hi) : []);
    x.lo.setData(band ? pts(band.lo) : []);
    x.spy.setData(pts(spy));
    x.you.setData(pts(you));
    const dates = new Set(you.map((p) => p.date));
    x.marks.setMarkers(events.filter((e) => dates.has(e.date)).map((e) => ({
      time: e.date as Time, position: "inBar" as const, shape: "circle" as const, size: 0.6, color: token("foreground"),
    })));
    chart.current?.timeScale().fitContent();
  }, [you, spy, band, events]);

  return <div ref={host} role="img" aria-label={label} className="w-full" style={{ height }} />;
}
