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
  /** Colour of your line: how the shown period ended. */
  tone: "gain" | "loss" | "flat";
  label: string;
  /** Called with the hovered date, or null when the pointer leaves. */
  onScrub?: (date: string | null) => void;
}

const pctLabel = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`;
const dayLabel = (t: Time) =>
  new Date(`${String(t)}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });

/**
 * Your return against the benchmark, both from 0% at the start of the range.
 * Your line takes the gain or loss colour; the benchmark stays a quiet grey.
 * Each line ends in a tag on the axis; the crosshair carries a date tag.
 */
export function ReturnChart({ you, spy, tone, label, onScrub }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const youS = useRef<ISeriesApi<"Area"> | null>(null);
  const spyS = useRef<ISeriesApi<"Line"> | null>(null);
  const toneRef = useRef(tone);
  const paintRef = useRef<() => void>(() => {});
  const scrub = useRef(onScrub);
  scrub.current = onScrub;
  toneRef.current = tone;

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
      lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: "S&P", crosshairMarkerRadius: 3,
    });
    youS.current = c.addSeries(AreaSeries, {
      lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: "You", crosshairMarkerRadius: 4,
      crosshairMarkerBorderWidth: 2,
    });
    const zero = youS.current.createPriceLine({ price: 0, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false });

    const paint = () => {
      const t = toneRef.current;
      const line = token(t === "loss" ? "loss" : t === "gain" ? "gain" : "ink-2");
      const fill = t === "loss" ? token("loss-fill") : t === "gain" ? token("gain-fill") : token("hover-wash");
      c.applyOptions({
        layout: { textColor: token("ink-3"), fontFamily: token("font-sans") },
        crosshair: {
          vertLine: { color: token("chart-crosshair"), width: 1, style: LineStyle.Dashed, labelBackgroundColor: token("ink") },
        },
      });
      youS.current!.applyOptions({
        lineColor: line, topColor: fill, bottomColor: "rgba(0,0,0,0)",
        crosshairMarkerBorderColor: token("surface"), crosshairMarkerBackgroundColor: line,
      });
      spyS.current!.applyOptions({
        color: token("chart-benchmark"), crosshairMarkerBackgroundColor: token("chart-benchmark"),
        crosshairMarkerBorderColor: token("surface"),
      });
      zero.applyOptions({ color: token("hairline-strong") });
    };
    paintRef.current = paint;
    paint();
    const off = onSchemeChange(paint);
    const move = (p: MouseEventParams<Time>) => scrub.current?.(p.time ? String(p.time) : null);
    c.subscribeCrosshairMove(move);
    chart.current = c;
    return () => { off(); c.unsubscribeCrosshairMove(move); c.remove(); chart.current = null; };
  }, []);

  useEffect(() => { paintRef.current(); }, [tone]);

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
