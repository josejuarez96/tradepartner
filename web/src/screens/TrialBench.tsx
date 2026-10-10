import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronLeft, Maximize2, Minimize2, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import type { AppData, Idea, Replay, ReplayRebalance } from "@/lib/types";
import { money, pct, tone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { BenchChart } from "@/components/BenchChart";
import { Scrubber } from "@/components/Scrubber";
import { LuckInfo } from "@/components/LuckInfo";
import { Assumptions, CostBars, SignalPicture, YearBars, p0, pts1, useWidth, yearDate } from "@/screens/Trial";

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
  const [focusMonth, setFocusMonth] = useState<string | null>(null);
  const [wide, setWide] = useState<string | null>(null);

  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setAt((k) => { if (k >= last) { setPlaying(false); return k; } return k + 1; }), fast ? 220 : 700);
    return () => clearInterval(t);
  }, [playing, fast, last]);
  // Space plays and pauses, unless a control has focus.
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key !== " " || (e.target as HTMLElement).closest("button, input, [role=slider], a")) return;
      e.preventDefault();
      setPlaying((p) => { if (!p) setAt((k) => (k >= last ? 0 : k)); return !p; });
    };
    addEventListener("keydown", on);
    return () => removeEventListener("keydown", on);
  }, [last]);

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
  // The month in focus, from the grid, points the chart at that month's last day.
  const focusDate = focusMonth ? [...series.you].reverse().find((p) => p.date.startsWith(focusMonth))?.date ?? null : null;
  const pointed = hover ?? focusDate;
  const shown = pointed && pointed <= until ? pointed : until;
  const k = series.you.findIndex((p) => p.date === shown);
  const youAt = series.you[k]?.value ?? 0, spyAt = series.spy[k]?.value ?? 0, ddAt = series.ddYou[k]?.value ?? 0;
  const r = R[at];
  const monthsDone = at === last ? replay.monthly.length : replay.monthly.filter((m) => `${m.month}-31` <= until).length;
  const seek = (i: number) => { setPlaying(false); setAt(i); };
  const span = (id: string, normal: string) => (wide === id ? "lg:col-span-12" : normal);
  const toggle = (id: string) => () => setWide(wide === id ? null : id);
  const luckWord = replay.end.luck >= 0.95 ? "likely real" : replay.end.luck >= 0.5 ? "could be luck" : "probably luck";

  return (
    <div className="mx-auto max-w-[1440px] px-4 pb-12 sm:px-6">
      <header className="flex flex-wrap items-end gap-x-8 gap-y-3 pt-4 pb-3">
        <div className="min-w-0">
          <a href={`#research/trial/${idea.id}`} className="text-muted-foreground hover:text-foreground -ml-1 inline-flex h-8 items-center gap-1 text-[12.5px]">
            <ChevronLeft className="size-3.5" />Simple view
          </a>
          <h1 className="text-[17px] font-medium tracking-[-0.01em]">{idea.name}, backtest</h1>
          <p className="text-muted-foreground text-[12.5px]">{yearDate(replay.window.start)} to {yearDate(replay.window.end)}, trial {replay.trial}</p>
        </div>
        <dl className="ml-auto grid grid-cols-2 gap-x-8 gap-y-2 sm:flex sm:gap-x-10">
          <Stat label="A year over the S&P, after costs"><span className={toneText[tone(replay.end.vs_spy)]}>{pts1(replay.end.vs_spy)} pts</span></Stat>
          <Stat label={<span className="inline-flex items-center gap-1">Luck check <LuckInfo idea={idea} /></span>}>{Math.round(replay.end.luck * 100)}% <span className="text-muted-foreground font-sans text-xs">{luckWord}</span></Stat>
          <Stat label="Versions tried">{replay.end.tries}</Stat>
          <Stat label="Exam on paper">{idea.exam?.kind === "paper" ? `${idea.exam.done} of ${idea.exam.of}` : "on hold"}</Stat>
        </dl>
      </header>

      {/* The transport stays in view while the panels scroll under it. */}
      <div className="bg-background sticky top-0 z-20 -mx-4 border-y px-4 py-2 sm:-mx-6 sm:px-6">
        <div className="flex items-center gap-2">
          <button onClick={() => { if (at >= last) setAt(0); setPlaying(!playing); }}
            className="bg-foreground text-background focus-visible:ring-ring/50 inline-flex h-11 shrink-0 items-center gap-1.5 rounded-full pr-3.5 pl-3 text-[12.5px] font-medium outline-none focus-visible:ring-2 sm:h-8">
            {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}{playing ? "Pause" : at === last ? "Replay" : "Play"}
          </button>
          <IconBtn label="Previous rebalance" disabled={at === 0} onClick={() => seek(at - 1)}><SkipBack className="size-3.5" /></IconBtn>
          <IconBtn label="Next rebalance" disabled={at === last} onClick={() => seek(at + 1)}><SkipForward className="size-3.5" /></IconBtn>
          <button aria-pressed={fast} onClick={() => setFast(!fast)} className={cn("h-11 shrink-0 rounded-full px-2.5 text-[12.5px] sm:h-8", fast ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground")}>2×</button>
          <Scrubber className="hidden min-w-0 flex-1 sm:block" values={series.you} stops={R.map((x) => x.date)} at={at} onSeek={seek} label={`Rebalance ${at + 1} of ${R.length}, ${yearDate(r.date)}`} />
          <span className="num text-muted-foreground ml-auto shrink-0 text-right text-[11.5px] leading-tight sm:ml-0 sm:w-28">{yearDate(r.date)}<br />{at + 1} of {R.length}</span>
        </div>
        <Scrubber className="mt-1 sm:hidden" values={series.you} stops={R.map((x) => x.date)} at={at} onSeek={seek} label={`Rebalance ${at + 1} of ${R.length}, ${yearDate(r.date)}`} />
      </div>

      <div className="mt-3 grid gap-2 lg:grid-cols-12">
        <Panel className={span("equity", "lg:col-span-8")} title="Equity and drawdown" wide={wide === "equity"} onWide={toggle("equity")}
          readout={<>{yearDate(shown)}<Read k="strategy" v={pct(youAt, 1)} cls={toneText[tone(youAt - spyAt)]} /><Read k="S&P" v={pct(spyAt, 1)} /><Read k="drawdown" v={pct(ddAt, 1)} /></>}>
          <BenchChart
            you={series.you} spy={series.spy} ddYou={series.ddYou} ddSpy={series.ddSpy} until={until} current={r.date} focusDate={hover ? null : focusDate}
            tone={tone(youAt - spyAt)} height={typeof window !== "undefined" && window.innerWidth < 640 ? 300 : wide === "equity" ? 520 : 420} onScrub={setHover}
            label={`Strategy ${pct(youAt, 1)} against the S&P 500 ${pct(spyAt, 1)} by ${yearDate(until)}; drawdown below.`}
          />
        </Panel>

        <Panel className={cn(span("inspector", "lg:col-span-4"), "lg:row-span-2")} title="This rebalance" wide={wide === "inspector"} onWide={toggle("inspector")} readout={`${at + 1} of ${R.length}`}>
          <Inspector r={r} first={at === 0} />
        </Panel>

        <Panel className={span("scores", "lg:col-span-4")} title="Scores that day" wide={wide === "scores"} onWide={toggle("scores")} readout={`cut ${p0(r.cut)}, ${r.hold} held`}>
          <div className="px-3 pb-3"><SignalPicture r={r} bins={replay.bins} caption={false} /></div>
        </Panel>

        <Panel className={span("mc", "lg:col-span-4")} title="Monte Carlo" wide={wide === "mc"} onWide={toggle("mc")} readout={`${Math.round(replay.monte_carlo.below_zero * 100)}% of ${replay.monte_carlo.runs.toLocaleString("en-US")} reshuffles end below zero`}>
          <MonteCarlo mc={replay.monte_carlo} upTo={monthsDone} />
        </Panel>

        <Panel className={span("months", "lg:col-span-8")} title="Month by month" wide={wide === "months"} onWide={toggle("months")} readout="points over the S&P">
          <MonthGrid replay={replay} upTo={monthsDone} active={(hover ?? "").slice(0, 7) || focusMonth} onFocus={setFocusMonth} />
        </Panel>

        <Panel className={span("tape", "lg:col-span-4")} title="Trades that day" wide={wide === "tape"} onWide={toggle("tape")} readout={at === 0 ? `${r.entries.length} bought` : `${r.entries.length} bought, ${r.exits.length} sold`}>
          <Tape r={r} first={at === 0} />
        </Panel>

        <Panel className={span("years", "lg:col-span-4")} title="By year" wide={wide === "years"} onWide={toggle("years")} readout="over the S&P">
          <div className="px-3 pb-2 [&_ul]:border-t-0"><YearBars replay={replay} /></div>
        </Panel>
        <Panel className={span("costs", "lg:col-span-4")} title="If trading cost more" wide={wide === "costs"} onWide={toggle("costs")} readout="points a year">
          <div className="px-3 pb-2 [&_ul]:border-t-0"><CostBars replay={replay} /></div>
        </Panel>
        <Panel className={span("props", "lg:col-span-4")} title="Properties" wide={wide === "props"} onWide={toggle("props")}>
          <div className="px-3 pb-2 [&_dl]:border-t-0"><Assumptions replay={replay} /></div>
        </Panel>
      </div>
      <p className="text-muted-foreground mt-3 text-xs">
        Trial {replay.trial} of h1-momentum-12-1, {idea.family} family; settings fingerprint and code version {replay.identity.params}; data as known through {yearDate(replay.identity.data_cutoff)}.
        Sample data: H1&apos;s rule and window, invented stocks and prices. Space plays and pauses; arrow keys on the timeline step.
      </p>
    </div>
  );
}

function Stat({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div>
      <dt className="text-muted-foreground text-[11.5px]">{label}</dt>
      <dd className="num text-[18px] leading-tight">{children}</dd>
    </div>
  );
}

function Read({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return <span className="ml-3"><span className="font-sans">{k}</span> <span className={cn("text-foreground", cls)}>{v}</span></span>;
}

function IconBtn({ label, disabled, onClick, children }: { label: string; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button aria-label={label} disabled={disabled} onClick={onClick}
      className="text-muted-foreground hover:text-foreground hover:bg-secondary focus-visible:ring-ring/50 grid size-11 shrink-0 place-items-center rounded-full outline-none focus-visible:ring-2 disabled:opacity-30 sm:size-8">
      {children}
    </button>
  );
}

/** A figure on the bench: a soft surface, a quiet title and a live readout, and a button to give it the full width. */
function Panel({ title, readout, className, wide, onWide, children }: { title: string; readout?: ReactNode; className?: string; wide: boolean; onWide: () => void; children: ReactNode }) {
  return (
    <section className={cn("bg-raised min-w-0 overflow-hidden rounded-[10px] border", className)}>
      <header className="flex min-h-10 flex-wrap items-center gap-x-3 px-3 pt-1 max-lg:pb-1">
        <h2 className="text-[12.5px] font-medium">{title}</h2>
        <span className="num text-muted-foreground min-w-0 flex-1 text-right text-[11.5px] max-lg:order-last max-lg:basis-full max-lg:text-left lg:truncate">{readout}</span>
        <button aria-label={wide ? `Shrink ${title}` : `Widen ${title}`} aria-pressed={wide} onClick={onWide}
          className="text-muted-foreground hover:text-foreground hover:bg-secondary hidden size-7 shrink-0 place-items-center rounded-md lg:grid">
          {wide ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
        </button>
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
    <div className="max-h-[230px] overflow-y-auto">
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
function MonthGrid({ replay, upTo, active, onFocus }: { replay: Replay; upTo: number; active: string | null; onFocus: (m: string | null) => void }) {
  const shown = replay.monthly.slice(0, upTo);
  const years = [...new Set(replay.monthly.map((m) => m.month.slice(0, 4)))];
  const max = Math.max(...replay.monthly.map((m) => Math.abs(m.s - m.b)));
  return (
    <div className="overflow-x-auto px-3 py-2">
      <table className="num w-full min-w-[600px] table-fixed border-separate border-spacing-[2px] text-[11.5px]">
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
              <tr key={y}>
                <td className="text-muted-foreground py-1 pr-2">{y}</td>
                {MONTHS.map((_, i) => {
                  const m = ms.find((x) => Number(x.month.slice(5)) === i + 1);
                  if (!m) return <td key={i} />;
                  const e = m.s - m.b, a = Math.min(0.45, (Math.abs(e) / max) * 0.45 + 0.04);
                  return (
                    <td key={i} tabIndex={0} onMouseEnter={() => onFocus(m.month)} onMouseLeave={() => onFocus(null)} onFocus={() => onFocus(m.month)} onBlur={() => onFocus(null)}
                      className={cn("cursor-default rounded-[3px] px-1 py-1 text-right outline-none", active === m.month && "ring-foreground ring-1 ring-inset")}
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
