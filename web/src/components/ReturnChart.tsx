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

/** Your return (ink, soft area) against the benchmark (quiet dashed line), both from 0%. */
export function ReturnChart({ you, spy, label, onScrub }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const youS = useRef<ISeriesApi<"Area"> | null>(null);
  const spyS = useRef<ISeriesApi<"Line"> | null>(null);
  const scrub = useRef(onScrub);
  scrub.current = onScrub;

  useEffect(() => {
    const el = host.current!;
    const c = createChart(el, {
      autoSize: true,
      handleScroll: false,
      handleScale: false,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, attributionLogo: false, fontSize: 11 },
      grid: { vertLines: { visible: false } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.14, bottom: 0.1 } },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
      crosshair: { mode: CrosshairMode.Magnet, horzLine: { visible: false, labelVisible: false } },
      localization: { priceFormatter: (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%` },
    });
    spyS.current = c.addSeries(LineSeries, {
      lineWidth: 1, lineStyle: LineStyle.Solid, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerRadius: 3,
    });
    youS.current = c.addSeries(AreaSeries, {
      lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 4,
      crosshairMarkerBorderWidth: 2,
    });
    const paint = () => {
      const f = token("font-sans");
      c.applyOptions({
        layout: { textColor: token("ink-3"), fontFamily: f },
        grid: { horzLines: { color: token("chart-grid") } },
        crosshair: { vertLine: { color: token("chart-crosshair"), width: 1, style: LineStyle.Solid, labelVisible: false } },
      });
      youS.current!.applyOptions({
        lineColor: token("chart-line"), topColor: token("chart-fill-top"), bottomColor: token("chart-fill-bottom"),
        crosshairMarkerBorderColor: token("surface"), crosshairMarkerBackgroundColor: token("chart-line"),
      });
      spyS.current!.applyOptions({ color: token("chart-benchmark"), crosshairMarkerBackgroundColor: token("chart-benchmark"),
        crosshairMarkerBorderColor: token("surface") });
    };
    const zero = youS.current.createPriceLine({ price: 0, lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: false, color: token("hairline-strong") });
    const paintZero = () => zero.applyOptions({ color: token("hairline-strong") });
    paint();
    paintZero();
    const off = onSchemeChange(() => { paint(); paintZero(); });
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
