import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronLeft } from "lucide-react";
import type { AppData, Idea, Replay, ReplayRebalance } from "@/lib/types";
import { money, pct, tone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { BenchChart } from "@/components/BenchChart";
import { LuckInfo } from "@/components/LuckInfo";
import { Assumptions, Controls, CostBars, SignalPicture, YearBars, p0, pts1, useWidth, yearDate } from "@/screens/Trial";

const toneText = { gain: "text-gain", loss: "text-loss", flat: "text-muted-foreground" } as const;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * The same backtest as a research workstation: tiled panels, each a figure
 * with a title and a live readout, the run's controls in one toolbar, and an
 * inspector that follows the playhead. The look of the bench software the
 * owner pointed at (Build Alpha, RealTest, AmiBroker), with honest forms:
 * percentile bands instead of spaghetti, numbers in every cell, no 3D.
 */
export function TrialBench({ data, ideaId }: { data: AppData; ideaId: string }) {
  const replay = data.research.replays[ideaId];
  const idea = data.research.ideas.find((i) => i.id === ideaId);
  if (!replay || !idea) return <p className="text-muted-foreground p-6">No backtest to show for this idea.</p>;
  return <Bench replay={replay} idea={idea} />;
}

function Bench({ replay, idea }: { replay: Replay; idea: Idea }) {
  const R = replay.rebalances;
  const last = R.length - 1;
  const [at, setAt] = useState(last);
  const [playing, setPlaying] = useState(false);
  const [fast, setFast] = useState(false);
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setAt((k) => { if (k >= last) { setPlaying(false); return k; } return k + 1; }), fast ? 220 : 700);
    return () => clearInterval(t);
  }, [playing, fast, last]);

  const series = useMemo(() => {
    const v0 = replay.days[0].v, b0 = replay.days[0].b;
    return {
      you: replay.days.map((d) => ({ date: d.date, value: d.v / v0 - 1 })),
      spy: replay.days.map((d) => ({ date: d.date, value: d.b / b0 - 1 })),
      ddYou: replay.drawdown.map((d) => ({ date: d.date, value: d.s })),
      ddSpy: replay.drawdown.map((d) => ({ date: d.date, value: d.b })),
    };
  }, [replay]);
  const until = at === last ? replay.window.end : R[at].date;
  const shown = hover && hover <= until ? hover : until;
  const k = series.you.findIndex((p) => p.date === shown);
  const youAt = series.you[k]?.value ?? 0, spyAt = series.spy[k]?.value ?? 0, ddAt = series.ddYou[k]?.value ?? 0;
  const r = R[at];
  const monthsDone = replay.monthly.filter((m) => `${m.month}-31` <= until || at === last).length;

  return (
    <div className="mx-auto max-w-[1440px] px-4 pt-3 pb-12 sm:px-6">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <a href={`#research/trial/${idea.id}`} className="text-muted-foreground hover:text-foreground -ml-1 inline-flex h-11 items-center gap-1 text-[13px] sm:h-8">
          <ChevronLeft className="size-4" />Simple view
        </a>
        <h1 className="text-[15px] font-medium">{idea.name}, backtest</h1>
        <span className="text-muted-foreground text-[13px]">{yearDate(replay.window.start)} to {yearDate(replay.window.end)}</span>
      </div>

      {/* Readouts: the result and whether to believe it, always in view. */}
      <dl className="bg-border mt-2 grid grid-cols-2 gap-px overflow-hidden rounded-md border sm:grid-cols-4">
        <Readout label="Over the S&P 500, a year after costs"><span className={toneText[tone(replay.end.vs_spy)]}>{pts1(replay.end.vs_spy)} pts</span></Readout>
        <Readout label={<span className="inline-flex items-center gap-1.5">Luck check <LuckInfo idea={idea} /></span>}>
          {Math.round(replay.end.luck * 100)}% <span className="text-muted-foreground font-sans text-xs">{replay.end.luck >= 0.95 ? "likely real" : replay.end.luck >= 0.5 ? "could be luck" : "probably luck"}</span>
        </Readout>
        <Readout label="Versions tried in the family">{replay.end.tries}</Readout>
        <Readout label="Exam on paper">{idea.exam?.kind === "paper" ? `${idea.exam.done} of ${idea.exam.of}` : "on hold"} <span className="text-muted-foreground font-sans text-xs">{idea.exam?.unit}</span></Readout>
      </dl>

      <div className="bg-border mt-3 grid gap-px overflow-hidden rounded-md border lg:grid-cols-12">
        <Panel className="lg:col-span-8" title="Equity and drawdown" readout={`${yearDate(shown)}   strategy ${pct(youAt, 1)}   S&P ${pct(spyAt, 1)}   drawdown ${pct(ddAt, 1)}`}>
          <BenchChart
            you={series.you} spy={series.spy} ddYou={series.ddYou} ddSpy={series.ddSpy} until={until} current={r.date}
            tone={tone(youAt - spyAt)} height={typeof window !== "undefined" && window.innerWidth < 640 ? 300 : 400} onScrub={setHover}
            label={`Strategy ${pct(youAt, 1)} against the S&P 500 ${pct(spyAt, 1)} by ${yearDate(until)}; drawdown below.`}
          />
          <div className="border-t px-3">
            <Controls at={at} total={R.length} date={r.date} playing={playing} fast={fast}
              onPlay={() => { if (at >= last) setAt(0); setPlaying(!playing); }}
              onStep={(d) => { setPlaying(false); setAt((x) => Math.max(0, Math.min(last, x + d))); }}
              onFast={() => setFast(!fast)} onSeek={(v) => { setPlaying(false); setAt(v); }} />
          </div>
        </Panel>

        <Panel className="lg:col-span-4 lg:row-span-2" title={`Rebalance ${at + 1} of ${R.length}`} readout={yearDate(r.date)}>
          <Inspector r={r} first={at === 0} />
        </Panel>

        <Panel className="lg:col-span-4" title="Scores that day" readout={`cut ${p0(r.cut)}, ${r.hold} held`}>
          <div className="px-3 pb-3"><SignalPicture r={r} bins={replay.bins} caption={false} /></div>
        </Panel>

        <Panel className="lg:col-span-4" title="Monte Carlo, monthly results reshuffled" readout={`${replay.monte_carlo.runs.toLocaleString("en-US")} runs, ${Math.round(replay.monte_carlo.below_zero * 100)}% end below zero`}>
          <MonteCarlo mc={replay.monte_carlo} upTo={monthsDone} />
        </Panel>

        <Panel className="lg:col-span-8" title="Month by month, over the S&P 500" readout="points; strategy minus S&P">
          <MonthGrid replay={replay} upTo={monthsDone} />
        </Panel>

        <Panel className="lg:col-span-4" title="Trades that day" readout={at === 0 ? `${r.entries.length} bought` : `${r.entries.length} bought, ${r.exits.length} sold`}>
          <Tape r={r} first={at === 0} />
        </Panel>

        <Panel className="lg:col-span-4" title="By year" readout="over the S&P 500">
          <div className="px-3 pb-2"><YearBars replay={replay} /></div>
        </Panel>
        <Panel className="lg:col-span-4" title="If trading cost more" readout="points a year over the S&P">
          <div className="px-3 pb-2"><CostBars replay={replay} /></div>
        </Panel>
        <Panel className="lg:col-span-4" title="Properties" readout={`trial ${replay.trial}`}>
          <div className="px-3 pb-2 [&_dl]:border-t-0"><Assumptions replay={replay} /></div>
        </Panel>
      </div>
      <p className="text-muted-foreground mt-3 text-xs">
        Trial {replay.trial} of h1-momentum-12-1, {idea.family} family; settings fingerprint and code version {replay.identity.params}; data as known through {yearDate(replay.identity.data_cutoff)}.
        Sample data: H1&apos;s rule and window, invented stocks and prices.
      </p>
    </div>
  );
}

function Readout({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="bg-background px-3 py-2">
      <dt className="text-muted-foreground text-[12px]">{label}</dt>
      <dd className="num mt-0.5 text-[17px]">{children}</dd>
    </div>
  );
}

/** A figure on the bench: a thin title bar with a live readout, then the figure. */
function Panel({ title, readout, className, children }: { title: string; readout?: ReactNode; className?: string; children: ReactNode }) {
  return (
    <section className={cn("bg-background min-w-0", className)}>
      <header className="flex min-h-9 flex-wrap items-baseline justify-between gap-x-3 border-b px-3 py-2">
        <h2 className="text-[12.5px] font-medium">{title}</h2>
        {readout && <span className="num text-muted-foreground text-[11.5px] whitespace-pre-wrap">{readout}</span>}
      </header>
      {children}
    </section>
  );
}

/** One rebalance, stage by stage, as a table; then the names around the cut. */
function Inspector({ r, first }: { r: ReplayRebalance; first: boolean }) {
  const excluded = r.excluded.reduce((a, x) => a + x.n, 0);
  const rows: [string, string][] = [
    ["1. Universe, largest US stocks", r.n_universe.toLocaleString("en-US")],
    ...r.excluded.map((x) => [`2. Excluded: ${x.rule.toLowerCase()}`, `−${x.n}`] as [string, string]),
    ["3. Ranked on past-year return", r.n_scored.toLocaleString("en-US")],
    ["4. Held, the top 10%", String(r.hold)],
    ["5. Bought", `+${r.entries.length}`],
    ...(first ? [] : [["5. Sold", `−${r.exits.length}`] as [string, string]]),
    ["6. Traded back to equal weight", String(r.reweighted)],
    ["7. Trading costs", money(r.cost_usd)],
  ];
  return (
    <div className="text-[12.5px]">
      <table className="w-full">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k} className="border-b"><td className="text-muted-foreground px-3 py-1.5">{k}</td><td className="num px-3 py-1.5 text-right">{v}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="text-muted-foreground px-3 pt-3 pb-1 text-[11.5px]">{excluded} excluded names had no score; everything at or above the dashed line is held.</p>
      <table className="num w-full">
        <thead>
          <tr className="text-muted-foreground border-y font-sans text-[11.5px]">
            <th className="px-3 py-1.5 text-right font-normal">Rank</th><th className="px-3 py-1.5 text-left font-normal">Stock</th><th className="px-3 py-1.5 text-right font-normal">Score</th>
          </tr>
        </thead>
        <tbody>
          {r.near.map((x, i) => {
            const lastHeld = x.held && !r.near[i + 1]?.held;
            return (
              <tr key={x.s} className={cn(lastHeld ? "border-foreground border-b border-dashed" : "", !x.held && "text-muted-foreground")}>
                <td className="px-3 py-1 text-right">{x.r}</td><td className="px-3 py-1">{x.s}</td><td className="px-3 py-1 text-right">{p0(x.sig!)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Every trade the rebalance made, as a tape: side, stock, the rank that caused it. */
function Tape({ r, first }: { r: ReplayRebalance; first: boolean }) {
  const rows = [
    ...r.exits.map((x) => ({ side: "Sell", s: x.s, why: x.r == null ? "left the universe" : `fell to rank ${x.r}` })),
    ...r.entries.map((x) => ({ side: "Buy", s: x.s, why: first ? `rank ${x.r}` : `rose to rank ${x.r}` })),
  ];
  return (
    <div className="max-h-[320px] overflow-y-auto">
      <table className="w-full text-[12.5px]">
        <thead className="bg-background sticky top-0">
          <tr className="text-muted-foreground border-b text-[11.5px]"><th className="px-3 py-1.5 text-left font-normal">Side</th><th className="px-3 py-1.5 text-left font-normal">Stock</th><th className="px-3 py-1.5 text-left font-normal">Why</th></tr>
        </thead>
        <tbody>
          {rows.map((x) => (
            <tr key={x.side + x.s} className="border-b"><td className="px-3 py-1">{x.side}</td><td className="num px-3 py-1">{x.s}</td><td className="text-muted-foreground px-3 py-1">{x.why}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="text-muted-foreground px-3 py-2 text-[11.5px]">Sells go first, then buys, all at the close; {r.reweighted} more small trades brought the kept names back to equal weight.</p>
    </div>
  );
}

/**
 * Where the result could have landed: the 40 monthly excess returns drawn
 * again in random order with replacement, a thousand times. Bands are the
 * middle half and the middle 90% of those runs; the line is what happened.
 */
function MonteCarlo({ mc, upTo }: { mc: Replay["monte_carlo"]; upTo: number }) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const H = 200, pad = { l: 8, r: 44, t: 10, b: 20 };
  const T = mc.bands.length;
  const lo = Math.min(...mc.bands.map((b) => b.p5), ...mc.actual, 0), hi = Math.max(...mc.bands.map((b) => b.p95), ...mc.actual, 0);
  const x = (t: number) => pad.l + (t / (T - 1)) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const area = (a: "p5" | "p25", b: "p95" | "p75") =>
    `M${mc.bands.map((v, t) => `${x(t)},${y(v[b])}`).join("L")}L${[...mc.bands].reverse().map((v, t) => `${x(T - 1 - t)},${y(v[a])}`).join("L")}Z`;
  const ticks = [-0.05, 0, 0.05, 0.1].filter((v) => v >= lo && v <= hi);
  const actual = mc.actual.slice(0, Math.max(1, upTo));
  const end = mc.actual.at(-1)!;
  return (
    <div ref={ref} className="px-1 pb-2">
      <svg width={W} height={H} className="block" role="img" aria-label={`Of ${mc.runs} reshuffled runs, the middle 90% end between ${pts1(mc.bands.at(-1)!.p5)} and ${pts1(mc.bands.at(-1)!.p95)} points; the actual path ends at ${pts1(end)}.`}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} className={v === 0 ? "stroke-ring" : "stroke-border"} />
            <text x={W - pad.r + 6} y={y(v) + 3.5} className="fill-muted-foreground font-mono text-[10.5px]">{pts1(v)}</text>
          </g>
        ))}
        {[0, 9, 19, 29, 39].filter((t) => t < T).map((t) => (
          <text key={t} x={x(t)} y={H - 5} textAnchor="middle" className="fill-muted-foreground font-mono text-[10.5px]">{t + 1}</text>
        ))}
        <path d={area("p5", "p95")} className="fill-foreground/[0.08]" />
        <path d={area("p25", "p75")} className="fill-foreground/[0.14]" />
        <path d={`M${mc.bands.map((v, t) => `${x(t)},${y(v.p50)}`).join("L")}`} className="stroke-muted-foreground" fill="none" strokeDasharray="3 3" />
        <path d={`M${actual.map((v, t) => `${x(t)},${y(v)}`).join("L")}`} className={tone(end) === "loss" ? "stroke-loss" : "stroke-gain"} fill="none" strokeWidth={2} />
        <text x={x(T - 1) - 4} y={y(mc.bands.at(-1)!.p95) + 12} textAnchor="end" className="fill-muted-foreground text-[10.5px]">90% of runs</text>
        <text x={x(T - 1) - 4} y={y(mc.bands.at(-1)!.p50) - 6} textAnchor="end" className="fill-muted-foreground text-[10.5px]">middle half</text>
      </svg>
      <p className="text-muted-foreground px-2 text-[11.5px]">Months along the bottom; cumulative points over the S&amp;P up the side. The line is what happened; the dashed line is the middle run.</p>
    </div>
  );
}

/** Years down, months across, each cell the month's excess in points, tinted by sign and size. A total per year at the end. */
function MonthGrid({ replay, upTo }: { replay: Replay; upTo: number }) {
  const shown = replay.monthly.slice(0, upTo);
  const years = [...new Set(replay.monthly.map((m) => m.month.slice(0, 4)))];
  const max = Math.max(...replay.monthly.map((m) => Math.abs(m.s - m.b)));
  return (
    <div className="overflow-x-auto px-3 py-2">
      <table className="num w-full min-w-[600px] table-fixed text-[11.5px]">
        <thead>
          <tr className="text-muted-foreground font-sans">
            <th className="w-12 py-1 text-left font-normal" />
            {MONTHS.map((m) => <th key={m} className="py-1 text-right font-normal">{m}</th>)}
            <th className="w-14 py-1 pl-2 text-right font-normal">Year</th>
          </tr>
        </thead>
        <tbody>
          {years.map((y) => {
            const ms = shown.filter((m) => m.month.startsWith(y));
            const total = ms.reduce((a, m) => a * (1 + m.s), 1) - ms.reduce((a, m) => a * (1 + m.b), 1);
            return (
              <tr key={y} className="border-t">
                <td className="text-muted-foreground py-1 pr-2">{y}</td>
                {MONTHS.map((_, i) => {
                  const m = ms.find((x) => Number(x.month.slice(5)) === i + 1);
                  if (!m) return <td key={i} />;
                  const e = m.s - m.b, a = Math.min(0.45, (Math.abs(e) / max) * 0.45 + 0.04);
                  return (
                    <td key={i} className="px-1 py-1 text-right"
                      style={{ background: `color-mix(in oklab, var(--${e >= 0 ? "gain" : "loss"}) ${Math.round(a * 100)}%, transparent)` }}>
                      {pts1(e)}
                    </td>
                  );
                })}
                <td className={cn("py-1 pl-2 text-right font-medium", ms.length ? toneText[tone(total)] : "")}>{ms.length ? pts1(total) : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
