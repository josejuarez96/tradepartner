import { useEffect, useRef } from "react";
import {
  AreaSeries, ColorType, CrosshairMode, LineSeries, LineStyle, createChart,
  type IChartApi, type ISeriesApi, type MouseEventParams, type Time,
} from "lightweight-charts";
import type { Point } from "../lib/types";
import { onSchemeChange, token } from "../lib/tokens";
import "./ReturnChart.css";

interface Props {
  you: Point[];
  spy: Point[];
  label: string;
  /** Called with the hovered date, or null when the pointer leaves. */
  onScrub?: (date: string | null) => void;
}

const pctLabel = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`;
const dayLabel = (t: Time) =>
  new Date(`${String(t)}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });

/**
 * Your return against the benchmark, both from 0% at the start of the range.
 * Your line is ink with a faint wash under it; the benchmark is a thin quiet
 * line. No tags on the lines: the legend above the chart prints both values
 * and follows the pointer.
 */
export function ReturnChart({ you, spy, label, onScrub }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const youS = useRef<ISeriesApi<"Area"> | null>(null);
  const spyS = useRef<ISeriesApi<"Line"> | null>(null);
  const scrub = useRef(onScrub);
  scrub.current = onScrub;

  useEffect(() => {
    const c = createChart(host.current!, {
      autoSize: true,
      handleScroll: false,
      handleScale: false,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, attributionLogo: false, fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.08 } },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
      crosshair: { mode: CrosshairMode.Magnet, horzLine: { visible: false, labelVisible: false } },
      localization: { priceFormatter: pctLabel, timeFormatter: dayLabel },
    });
    spyS.current = c.addSeries(LineSeries, {
      lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 3,
    });
    youS.current = c.addSeries(AreaSeries, {
      lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 4,
      crosshairMarkerBorderWidth: 2,
    });
    const zero = youS.current.createPriceLine({ price: 0, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false });

    const paint = () => {
      const line = token("chart-you");
      c.applyOptions({
        layout: { textColor: token("ink-3"), fontFamily: token("font-mono") },
        crosshair: {
          vertLine: { color: token("chart-crosshair"), width: 1, style: LineStyle.Dashed, labelBackgroundColor: token("ink") },
        },
      });
      youS.current!.applyOptions({
        lineColor: line, topColor: token("chart-you-fill"), bottomColor: "rgba(0,0,0,0)",
        crosshairMarkerBorderColor: token("bg"), crosshairMarkerBackgroundColor: line,
      });
      spyS.current!.applyOptions({
        color: token("chart-bench"), crosshairMarkerBackgroundColor: token("chart-bench"),
        crosshairMarkerBorderColor: token("bg"),
      });
      zero.applyOptions({ color: token("rule-strong") });
    };
    paint();
    const off = onSchemeChange(paint);
    const move = (p: MouseEventParams<Time>) => scrub.current?.(p.time ? String(p.time) : null);
    c.subscribeCrosshairMove(move);
    chart.current = c;
    return () => { off(); c.unsubscribeCrosshairMove(move); c.remove(); chart.current = null; };
  }, []);

  useEffect(() => {
    youS.current?.setData(you.map((p) => ({ time: p.date as Time, value: p.value })));
    spyS.current?.setData(spy.map((p) => ({ time: p.date as Time, value: p.value })));
    chart.current?.timeScale().fitContent();
  }, [you, spy]);

  return (
    <div className="rchart">
      <div ref={host} className="rchart__plot" role="img" aria-label={label} />
    </div>
  );
}
