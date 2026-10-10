import { useEffect, useRef } from "react";
import {
  AreaSeries, ColorType, CrosshairMode, LineSeries, LineStyle, createChart, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type ISeriesMarkersPluginApi, type MouseEventParams, type Time,
} from "lightweight-charts";
import type { Point } from "@/lib/types";
import { onThemeChange, token } from "@/lib/tokens";

interface Props {
  you: Point[];
  spy: Point[];
  /** Fall from the running peak, drawn in a pane under the equity, on the same time axis. */
  ddYou: Point[];
  ddSpy: Point[];
  until: string;
  current?: string;
  tone: "gain" | "loss" | "flat";
  label: string;
  height: number;
  onScrub?: (date: string | null) => void;
}

const pctLabel = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(0)}%`;
const dayLabel = (t: Time) =>
  new Date(`${String(t)}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

/**
 * The workstation's equity chart: gridlines and framed scales, the strategy
 * and the S&P above, the drawdown of each below in its own pane on the same
 * time axis, drawn up to the playhead with the axes fixed.
 */
export function BenchChart({ you, spy, ddYou, ddSpy, until, current, tone, label, height, onScrub }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const s = useRef<{
    you: ISeriesApi<"Line">; spy: ISeriesApi<"Line">; scale: ISeriesApi<"Line">;
    dd: ISeriesApi<"Area">; ddSpy: ISeriesApi<"Line">; ddScale: ISeriesApi<"Line">; marks: ISeriesMarkersPluginApi<Time>;
  } | null>(null);
  const toneRef = useRef(tone);
  toneRef.current = tone;
  const paintRef = useRef<() => void>(() => {});
  const scrub = useRef(onScrub);
  scrub.current = onScrub;

  useEffect(() => {
    const c = createChart(host.current!, {
      autoSize: true, handleScroll: false, handleScale: false,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, attributionLogo: false, fontSize: 10.5, panes: { enableResize: false } },
      rightPriceScale: { borderVisible: true, scaleMargins: { top: 0.06, bottom: 0.06 } },
      timeScale: { borderVisible: true, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
      crosshair: { mode: CrosshairMode.Magnet },
      localization: { priceFormatter: pctLabel, timeFormatter: dayLabel },
    });
    const common = { priceLineVisible: false, lastValueVisible: false } as const;
    const scale = c.addSeries(LineSeries, { ...common, color: "transparent", crosshairMarkerVisible: false });
    const spyS = c.addSeries(LineSeries, { ...common, lineWidth: 1, lineStyle: LineStyle.Dashed, crosshairMarkerRadius: 3 });
    const youS = c.addSeries(LineSeries, { ...common, lineWidth: 2, crosshairMarkerRadius: 3 });
    const ddScale = c.addSeries(LineSeries, { ...common, color: "transparent", crosshairMarkerVisible: false }, 1);
    const ddSpyS = c.addSeries(LineSeries, { ...common, lineWidth: 1, lineStyle: LineStyle.Dashed, crosshairMarkerVisible: false }, 1);
    const dd = c.addSeries(AreaSeries, { ...common, lineWidth: 1, crosshairMarkerVisible: false }, 1);
    c.panes()[0].setStretchFactor(3);
    c.panes()[1].setStretchFactor(1);
    s.current = { you: youS, spy: spyS, scale, dd, ddSpy: ddSpyS, ddScale, marks: createSeriesMarkers(youS, []) };
    const paint = () => {
      const t = toneRef.current;
      const line = token(t === "loss" ? "loss" : t === "flat" ? "muted-foreground" : "gain");
      const rule = token("border");
      c.applyOptions({
        layout: { textColor: token("muted-foreground"), fontFamily: "IBM Plex Mono, ui-monospace, monospace", panes: { separatorColor: rule } },
        grid: { vertLines: { color: rule }, horzLines: { color: rule } },
        rightPriceScale: { borderColor: rule },
        timeScale: { borderColor: rule },
        crosshair: {
          vertLine: { color: token("ring"), width: 1, style: LineStyle.Dashed, labelBackgroundColor: token("secondary") },
          horzLine: { color: token("ring"), width: 1, style: LineStyle.Dashed, labelBackgroundColor: token("secondary") },
        },
      });
      spyS.applyOptions({ color: token("bench") });
      youS.applyOptions({ color: line, crosshairMarkerBackgroundColor: line, crosshairMarkerBorderColor: token("background") });
      ddSpyS.applyOptions({ color: token("bench") });
      dd.applyOptions({ lineColor: token("loss"), topColor: token("loss", 0.08), bottomColor: token("loss", 0.35), invertFilledArea: true });
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
    x.scale.setData(you.map((p, i) => ({ time: p.date as Time, value: i % 2 ? p.value : spy[i]?.value ?? p.value })));
    x.ddScale.setData(ddYou.map((p, i) => ({ time: p.date as Time, value: i % 2 ? p.value : ddSpy[i]?.value ?? p.value })));
    chart.current?.timeScale().fitContent();
  }, [you, spy, ddYou, ddSpy]);

  useEffect(() => {
    const x = s.current;
    if (!x) return;
    const cut = (pts: Point[]) => pts.map((p) => (p.date <= until ? { time: p.date as Time, value: p.value } : { time: p.date as Time }));
    x.you.setData(cut(you));
    x.spy.setData(cut(spy));
    x.dd.setData(cut(ddYou));
    x.ddSpy.setData(cut(ddSpy));
    x.marks.setMarkers(current && current <= until ? [{ time: current as Time, position: "inBar" as const, shape: "circle" as const, size: 1, color: token("foreground") }] : []);
  }, [you, spy, ddYou, ddSpy, until, current]);

  return <div ref={host} role="img" aria-label={label} className="w-full" style={{ height }} />;
}
