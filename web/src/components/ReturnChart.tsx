import { useEffect, useRef } from "react";
import {
  AreaSeries, ColorType, CrosshairMode, LineSeries, LineStyle, createChart,
  type IChartApi, type ISeriesApi, type MouseEventParams, type Time,
} from "lightweight-charts";
import type { Point } from "@/lib/types";
import { onThemeChange, token } from "@/lib/tokens";

interface Props {
  you: Point[];
  spy: Point[];
  label: string;
  /** Called with the hovered date, or null when the pointer leaves. */
  onScrub?: (date: string | null) => void;
  height?: number;
}

const pctLabel = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`;
const dayLabel = (t: Time) =>
  new Date(`${String(t)}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });

/** Your return (chart-1, soft area) against the S&P 500 (chart-2, thin line), both from 0%. */
export function ReturnChart({ you, spy, label, onScrub, height = 280 }: Props) {
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
      grid: { vertLines: { visible: false } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.05 } },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
      crosshair: { mode: CrosshairMode.Magnet, horzLine: { visible: false, labelVisible: false } },
      localization: { priceFormatter: pctLabel, timeFormatter: dayLabel },
    });
    spyS.current = c.addSeries(LineSeries, { lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 3 });
    youS.current = c.addSeries(AreaSeries, { lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 4 });
    const zero = youS.current.createPriceLine({ price: 0, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false });

    const paint = () => {
      c.applyOptions({
        layout: { textColor: token("muted-foreground"), fontFamily: "Geist Variable, ui-sans-serif, sans-serif" },
        grid: { horzLines: { color: token("border", 0.6) } },
        crosshair: { vertLine: { color: token("ring"), width: 1, style: LineStyle.Solid, labelBackgroundColor: token("primary") } },
      });
      youS.current!.applyOptions({
        lineColor: token("chart-1"), topColor: token("chart-1", 0.16), bottomColor: token("chart-1", 0),
        crosshairMarkerBorderColor: token("background"), crosshairMarkerBackgroundColor: token("chart-1"),
      });
      spyS.current!.applyOptions({
        color: token("chart-2"), crosshairMarkerBackgroundColor: token("chart-2"), crosshairMarkerBorderColor: token("background"),
      });
      zero.applyOptions({ color: token("muted-foreground", 0.5) });
    };
    paint();
    const off = onThemeChange(paint);
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

  return <div ref={host} role="img" aria-label={label} className="w-full" style={{ height }} />;
}
