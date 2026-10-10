import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import type { AppData, Idea, Replay, ReplayRebalance } from "@/lib/types";
import { money, pct, shortDate, tone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Page, RailHead, Section } from "@/components/Shell";
import { ReplayChart } from "@/components/ReplayChart";
import { LuckInfo } from "@/components/LuckInfo";
import { ExamSteps, LuckScale } from "@/components/Visuals";

const toneText = { gain: "text-gain", loss: "text-loss", flat: "text-muted-foreground" } as const;
export const pts1 = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}`;
export const yearDate = (iso: string) => `${shortDate(iso)}, ${iso.slice(0, 4)}`;
/** A whole-percent return with a true minus sign: "+50%", "0%", "−20%". */
export const p0 = (v: number) => { const n = Math.round(v * 100); return `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n)}%`; };
const narrow = () => typeof window !== "undefined" && window.innerWidth < 640;

/**
 * A backtest, opened: what it found, whether that could be luck, and the
 * engine running it. Press play (or step) and the run moves one rebalance at a
 * time; the chart draws forward and the pipeline below shows what the engine
 * did on that date, stage by stage.
 */
export function Trial({ data, ideaId }: { data: AppData; ideaId: string }) {
  const replay = data.research.replays[ideaId];
  const idea = data.research.ideas.find((i) => i.id === ideaId);
  if (!replay || !idea) return <NoTrial />;
  return <TrialView replay={replay} idea={idea} />;
}

function TrialView({ replay, idea }: { replay: Replay; idea: Idea }) {
  const R = replay.rebalances;
  const last = R.length - 1;
  const [at, setAt] = useState(last);
  const [playing, setPlaying] = useState(false);
  const [fast, setFast] = useState(false);

  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setAt((k) => { if (k >= last) { setPlaying(false); return k; } return k + 1; }), fast ? 220 : 700);
    return () => clearInterval(t);
  }, [playing, fast, last]);

  const play = () => { if (at >= last) setAt(0); setPlaying(!playing); };
  const step = (d: number) => { setPlaying(false); setAt((k) => Math.max(0, Math.min(last, k + d))); };

  const series = useMemo(() => {
    const v0 = replay.days[0].v, b0 = replay.days[0].b;
    return {
      you: replay.days.map((d) => ({ date: d.date, value: d.v / v0 - 1 })),
      spy: replay.days.map((d) => ({ date: d.date, value: d.b / b0 - 1 })),
    };
  }, [replay]);
  const until = at === last ? replay.window.end : R[at].date;
  const k = series.you.findIndex((p) => p.date === until);
  const youAt = series.you[k]?.value ?? 0, spyAt = series.spy[k]?.value ?? 0;
  const r = R[at];
  const finalTone = tone(replay.end.vs_spy);

  return (
    <Page rail={<div className="hidden lg:block"><Rail r={r} at={at} /></div>}>
      <a href="#research" className="text-muted-foreground hover:text-foreground -ml-1 inline-flex h-11 items-center gap-1 text-[13px] sm:h-8">
        <ChevronLeft className="size-4" />Research
      </a>
      <a href={`#research/bench/${idea.id}`} className="text-muted-foreground hover:text-foreground float-right inline-flex h-11 items-center text-[13px] sm:h-8">Workstation view</a>
      <h1 className="text-muted-foreground mt-1">{idea.name}, backtest</h1>
      <p className={cn("num mt-0.5 text-[34px] font-medium tracking-[-0.03em] sm:text-[40px]", toneText[finalTone])}>{pts1(replay.end.vs_spy)} pts a year</p>
      <p className="text-muted-foreground">against the S&amp;P 500 after costs, {yearDate(replay.window.start)} to {yearDate(replay.window.end)}</p>

      <dl className="mt-6 grid gap-x-8 gap-y-5 border-y py-4 sm:grid-cols-[1.3fr_0.7fr_1fr]">
        <div>
          <dt className="text-muted-foreground mb-1.5 flex items-center gap-2 text-[12.5px]">Luck check <LuckInfo idea={idea} /></dt>
          <dd><LuckScale v={replay.end.luck} /></dd>
        </div>
        <div className="sm:border-l sm:pl-8">
          <dt className="text-muted-foreground mb-1.5 text-[12.5px]">Versions tried in the family</dt>
          <dd className="num text-xl">{replay.end.tries}</dd>
        </div>
        <div className="sm:border-l sm:pl-8">
          <dt className="text-muted-foreground mb-1.5 text-[12.5px]">The exam, on paper</dt>
          <dd>{idea.exam?.kind === "paper" ? <ExamSteps done={idea.exam.done!} of={idea.exam.of!} unit={idea.exam.unit!} /> : <span className="text-muted-foreground">{idea.exam?.note ?? "Not started"}</span>}</dd>
        </div>
      </dl>

      <div className="mt-6">
        <ReplayChart
          you={series.you} spy={series.spy} until={until} marks={at === last ? [] : [r.date]} current={r.date}
          tone={tone(youAt - spyAt)} height={narrow() ? 220 : 300}
          label={`Strategy ${pct(youAt, 1)} against the S&P 500 ${pct(spyAt, 1)} by ${yearDate(until)}.`}
        />
      </div>
      <p className="num text-muted-foreground mt-2 flex flex-wrap gap-x-5 text-xs">
        <span className={toneText[tone(youAt - spyAt)]}>Strategy {pct(youAt, 1)}</span>
        <span>S&amp;P 500 {pct(spyAt, 1)}</span>
      </p>

      <Controls at={at} total={R.length} date={r.date} playing={playing} fast={fast}
        onPlay={play} onStep={step} onFast={() => setFast(!fast)} onSeek={(v) => { setPlaying(false); setAt(v); }} />

      <Section title={`What the engine did on ${yearDate(r.date)}`} note={at === 0 ? "the first rebalance buys everything" : undefined}>
        <Pipeline r={r} first={at === 0} measure="past-year return" />
        <SignalPicture r={r} bins={replay.bins} />
        <div className="mt-6 lg:hidden"><Rail r={r} at={at} /></div>
      </Section>

      <Section title="One year at a time" note="excess over the S&P 500, after costs">
        <YearBars replay={replay} />
      </Section>

      <Section title="If trading cost more" note="points a year over the S&P 500, the same run at each cost level the engine evaluates">
        <CostBars replay={replay} />
      </Section>

      <Section title="Where this test sits">
        <Timeline replay={replay} idea={idea} />
      </Section>

      <Section title="Assumptions">
        <Assumptions replay={replay} />
      </Section>

      <p className="text-muted-foreground mt-10 border-t pt-4 text-xs">
        Trial {replay.trial} of hypothesis h1-momentum-12-1, {idea.family} family. Settings fingerprint and code version: {replay.identity.params}.
        Data as known through {yearDate(replay.identity.data_cutoff)}. Sample data: the rule and window are H1&apos;s, the stocks and prices are invented.
      </p>
    </Page>
  );
}

export function Controls({ at, total, date, playing, fast, onPlay, onStep, onFast, onSeek }: {
  at: number; total: number; date: string; playing: boolean; fast: boolean;
  onPlay: () => void; onStep: (d: number) => void; onFast: () => void; onSeek: (v: number) => void;
}) {
  const btn = "text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 grid size-11 place-items-center rounded-full outline-none focus-visible:ring-2 disabled:opacity-35 sm:size-9";
  return (
    <div className="mt-3 border-b pb-4">
      <div className="flex items-center gap-1">
        <button onClick={onPlay} className="bg-secondary hover:bg-accent focus-visible:ring-ring/50 inline-flex h-11 items-center gap-2 rounded-full pr-4 pl-3 text-[13px] font-medium outline-none focus-visible:ring-2 sm:h-9">
          {playing ? <Pause className="size-4" /> : <Play className="size-4" />}{playing ? "Pause" : at === total - 1 ? "Watch it run" : "Play"}
        </button>
        <button aria-label="Previous rebalance" className={btn} disabled={at === 0} onClick={() => onStep(-1)}><SkipBack className="size-4" /></button>
        <button aria-label="Next rebalance" className={btn} disabled={at === total - 1} onClick={() => onStep(1)}><SkipForward className="size-4" /></button>
        <button aria-pressed={fast} onClick={onFast} className={cn("ml-1 h-11 rounded-full px-3 text-[13px] sm:h-9", fast ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground")}>Fast</button>
        <span className="num text-muted-foreground ml-auto text-xs">{at + 1} of {total}</span>
      </div>
      <input
        type="range" min={0} max={total - 1} value={at} onChange={(e) => onSeek(Number(e.target.value))}
        aria-label="Rebalance" aria-valuetext={`Rebalance ${at + 1} of ${total}, ${yearDate(date)}`}
        className="accent-foreground focus-visible:ring-ring/50 mt-2 h-6 w-full cursor-pointer rounded-full outline-none focus-visible:ring-2"
      />
    </div>
  );
}

/**
 * The engine's stages for one rebalance, left to right (top to bottom on a
 * phone), joined by one line: the universe, what was excluded, what was
 * scored, what is held, the names in and out, the trades back to equal
 * weight, and what it cost.
 */
function Pipeline({ r, first, measure }: { r: ReplayRebalance; first: boolean; measure: string }) {
  const excluded = r.excluded.reduce((a, x) => a + x.n, 0);
  const stages: { n: string; label: string; title?: string }[] = [
    { n: r.n_universe.toLocaleString("en-US"), label: "largest US stocks" },
    { n: `−${excluded}`, label: "excluded", title: r.excluded.map((x) => `${x.rule}: ${x.n}`).join("; ") },
    { n: r.n_scored.toLocaleString("en-US"), label: `ranked on ${measure}` },
    { n: String(r.hold), label: "held, the top 10%" },
    { n: first ? `+${r.entries.length}` : `+${r.entries.length} −${r.exits.length}`, label: first ? "bought" : "bought, sold" },
    { n: String(r.reweighted), label: "traded back to equal weight" },
    { n: money(r.cost_usd).replace(/\.\d\d$/, ""), label: "trading costs" },
  ];
  return (
    <ol className="relative grid gap-y-2.5 border-l pl-5 sm:grid-cols-7 sm:gap-x-3 sm:border-t sm:border-l-0 sm:pt-4 sm:pl-0" aria-label="The engine's stages for this rebalance">
      {stages.map((s) => (
        <li key={s.label} className="relative flex items-baseline gap-3 sm:block" title={s.title}>
          <span className="bg-foreground ring-background absolute top-2.5 -left-[23.5px] size-2 rounded-full ring-2 sm:-top-[20.5px] sm:left-0" aria-hidden />
          <span className="num block w-20 shrink-0 text-lg leading-tight sm:w-auto">{s.n}</span>
          <span className="text-muted-foreground block text-[12.5px] leading-snug">{s.label}</span>
        </li>
      ))}
    </ol>
  );
}

export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useLayoutEffect(() => {
    const el = ref.current!;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/**
 * How the scores spread that day, and where the cut fell: every stock scored
 * is counted in a bar by its past-year return; bars past the cut are the
 * names held. Labels say which side is which, so there is no legend.
 */
export function SignalPicture({ r, bins, caption = true }: { r: ReplayRebalance; bins: Replay["bins"]; caption?: boolean }) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const H = 140;
  const max = Math.max(...r.hist);
  const bw = W / bins.n;
  const xOf = (v: number) => ((v - bins.lo) / (bins.hi - bins.lo)) * W;
  const cx = xOf(r.cut);
  const ticks = [-0.5, 0, 0.5, 1, 1.5];
  return (
    <figure className={caption ? "mt-7" : "mt-3"}>
      <figcaption className={caption ? "mb-2 text-[13px]" : "sr-only"}>
        Every stock scored, by its past-year return. The engine holds everything to the right of the cut, a return of <span className="num">{p0(r.cut)}</span> or more.
      </figcaption>
      <div ref={ref} className="relative" role="img" aria-label={`${r.n_scored} stocks scored; the cut is at ${p0(r.cut)}; ${r.hold} held.`}>
        <svg width={W} height={H} className="block">
          {r.hist.map((c, i) => {
            const x0 = i * bw, held = x0 + bw / 2 >= cx;
            const h = max ? (c / max) * (H - 18) : 0;
            return <rect key={i} x={x0 + 1} y={H - h} width={Math.max(1, bw - 2)} height={h} className={held ? "fill-foreground/75" : "fill-foreground/20"} />;
          })}
          <line x1={cx} x2={cx} y1={0} y2={H} className="stroke-foreground" strokeWidth={1.5} strokeDasharray="3 3" />
        </svg>
        <span className="text-muted-foreground absolute top-0 text-[11px] whitespace-nowrap" style={{ right: Math.max(0, W - cx + 6) }}>not held</span>
        <span className="absolute top-0 text-[11px] whitespace-nowrap" style={{ left: Math.min(W - 70, cx + 6) }}>held: {r.hold}</span>
        <div className="num text-muted-foreground relative mt-1 h-4 text-[11px]">
          {ticks.map((t) => <span key={t} className="absolute -translate-x-1/2" style={{ left: xOf(t) }}>{p0(t)}</span>)}
        </div>
      </div>
    </figure>
  );
}

function Rail({ r, at }: { r: ReplayRebalance; at: number }) {
  return (
    <div>
      <RailHead>At the cut, {shortDate(r.date)}</RailHead>
      <ol className="num text-[13px]">
        {r.near.map((x, i) => {
          const lastHeld = x.held && !r.near[i + 1]?.held;
          return (
            <li key={x.s} className={cn("flex items-center gap-3 py-1", lastHeld && "border-foreground border-b border-dashed pb-1.5")}>
              <span className="text-muted-foreground w-8 text-right">{x.r}</span>
              <span className={cn("w-14", !x.held && "text-muted-foreground")}>{x.s}</span>
              <span className={cn("ml-auto", !x.held && "text-muted-foreground")}>{p0(x.sig!)}</span>
            </li>
          );
        })}
      </ol>
      <p className="text-muted-foreground mt-1 text-xs">Held above the dashed line, the cut at rank {r.hold}.</p>

      {at > 0 && (
        <>
          <RailHead>Bought, at rank</RailHead>
          <p className="num text-[13px] leading-relaxed">{r.entries.map((x) => `${x.s} ${x.r}`).join(", ")}</p>
          <RailHead>Sold, at rank</RailHead>
          <p className="num text-[13px] leading-relaxed">{r.exits.map((x) => (x.r == null ? `${x.s} (left the universe)` : `${x.s} ${x.r}`)).join(", ")}</p>
        </>
      )}
      <RailHead>Top of the ranking</RailHead>
      <ol className="num text-[13px]">
        {r.top.map((x) => (
          <li key={x.s} className="flex gap-3 py-1"><span className="text-muted-foreground w-8 text-right">{x.r}</span><span className="w-14">{x.s}</span><span className="ml-auto">{p0(x.sig!)}</span></li>
        ))}
      </ol>
    </div>
  );
}

/** One row per calendar year: the strategy's return minus the S&P's, as a bar from a centre line. */
export function YearBars({ replay }: { replay: Replay }) {
  const rows = useMemo(() => {
    const years = [...new Set(replay.days.map((d) => d.date.slice(0, 4)))];
    return years.map((y) => {
      const ds = replay.days.filter((d) => d.date.slice(0, 4) === y);
      const prev = replay.days[replay.days.indexOf(ds[0]) - 1] ?? ds[0];
      const a = ds.at(-1)!.v / prev.v - 1, b = ds.at(-1)!.b / prev.b - 1;
      const partial = ds[0].date > `${y}-01-05` ? `${y}, from ${shortDate(ds[0].date)}` : y;
      return { y: partial, excess: a - b };
    });
  }, [replay]);
  return <Bars rows={rows.map((x) => ({ label: x.y, v: x.excess }))} />;
}

export function CostBars({ replay }: { replay: Replay }) {
  return (
    <>
      <Bars rows={replay.cost_levels.map((c) => ({ label: `${c.bp} bp a side${c.bp === replay.rule.per_side_bps ? ", assumed" : ""}`, v: c.vs_spy, strong: c.bp === replay.rule.per_side_bps }))} />
      <p className="text-muted-foreground mt-2 max-w-[70ch] text-xs">
        The strategy trades about {Math.round(replay.annual_turnover * 100)}% of its value a year, so every extra basis point of cost takes about {(replay.annual_turnover / 100).toFixed(2)} points a year off the result.
      </p>
    </>
  );
}

/** Rows of signed values as bars from a centre line, each with its number beside it. */
export function Bars({ rows, unit = "" }: { rows: { label: string; v: number; strong?: boolean }[]; unit?: string }) {
  const m = Math.max(...rows.map((r) => Math.abs(r.v)), 0.001);
  return (
    <ul className="border-t">
      {rows.map((r) => {
        const w = (Math.abs(r.v) / m) * 50;
        const t = tone(r.v);
        return (
          <li key={r.label} className={cn("grid min-h-11 grid-cols-[minmax(0,9rem)_1fr_5.5rem] items-center gap-3 border-b text-[13px] sm:grid-cols-[12rem_1fr_6.5rem]", r.strong && "font-medium")}>
            <span className={cn("truncate", !r.strong && "text-muted-foreground")}>{r.label}</span>
            <span className="relative h-3">
              <span className="bg-ring/60 absolute inset-y-[-4px] left-1/2 w-px" aria-hidden />
              <span className={cn("absolute inset-y-0 rounded-sm", t === "loss" ? "bg-loss/70" : "bg-gain/70")} style={r.v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
            </span>
            <span className={cn("num text-right", toneText[t])}>{pts1(r.v)} pts{unit}</span>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The idea's life on one track, each stretch named on the track itself: the
 * development years this test used, the final exam window kept away from it,
 * and the paper book that judges it now. Shades, not colours, and no legend.
 */
function Timeline({ replay, idea }: { replay: Replay; idea: Idea }) {
  const start = Date.parse(replay.window.start), end = Date.parse("2027-04-30");
  const x = (iso: string) => ((Date.parse(iso) - start) / (end - start)) * 100;
  const segs = [
    { from: replay.window.start, to: replay.window.end, label: "Development", cls: "bg-foreground/70" },
    { from: replay.holdout.start, to: replay.holdout.end, label: "Final exam window", cls: "bg-foreground/25" },
    { from: "2026-10-09", to: "2027-04-30", label: "On paper", cls: "bg-foreground/45", right: true },
  ];
  const ticks = [replay.window.start, replay.holdout.start, "2026-10-09"];
  return (
    <div role="img" aria-label={`Development ${yearDate(replay.window.start)} to ${yearDate(replay.window.end)}; final exam window ${yearDate(replay.holdout.start)} to ${yearDate(replay.holdout.end)}; on paper since Oct 9, 2026.`}>
      <div className="relative h-5 text-[12px]">
        {segs.map((s) => (
          <span key={s.label} className="absolute bottom-1 whitespace-nowrap" style={s.right ? { right: 0 } : { left: `${x(s.from)}%` }}>{s.label}</span>
        ))}
      </div>
      <div className="relative h-2.5">
        {segs.map((s) => <span key={s.label} className={cn("absolute inset-y-0 rounded-sm", s.cls)} style={{ left: `${x(s.from)}%`, width: `${Math.max(1.5, x(s.to) - x(s.from))}%` }} />)}
      </div>
      <div className="num text-muted-foreground relative mt-1 h-4 text-[11px]">
        {ticks.map((t, i) => <span key={t} className="absolute whitespace-nowrap" style={i === 2 ? { right: 0 } : { left: `${x(t)}%` }}>{i === 2 ? "Oct 2026" : t.slice(0, 4)}</span>)}
      </div>
      <p className="text-muted-foreground mt-3 max-w-[70ch] text-[13px]">
        This test and every version tried used only the development years. The final exam window is kept away from development and can be used once.
        {idea.exam?.kind === "paper" && <> On paper it has done {idea.exam.done} of {idea.exam.of} {idea.exam.unit}.</>}
      </p>
    </div>
  );
}

export function Assumptions({ replay }: { replay: Replay }) {
  const r = replay.rule;
  const rows: [string, string][] = [
    ["Universe", `The ${r.universe.toLocaleString("en-US")} largest US stocks, rebuilt at every rebalance from data as it was known that day; stocks that later delisted are included`],
    ["Signal", `${r.measure.charAt(0).toUpperCase()}${r.measure.slice(1)}`],
    ["Holdings", `The top ${Math.round(r.top_fraction * 100)}% of ranked stocks, equal weight, long only; the rest in cash`],
    ["Rebalance", "The last trading day of each month"],
    ["Fills", `At the ${r.fill_price}; sells first, then buys`],
    ["Costs", `${r.per_side_bps} bp a side on every trade`],
    ["Benchmark", "S&P 500 (SPY), bought once, dividends reinvested"],
  ];
  return (
    <dl className="border-t text-[13px]">
      {rows.map(([k, v]) => (
        <div key={k} className="grid gap-x-6 border-b py-2.5 sm:grid-cols-[9rem_1fr]">
          <dt className="text-muted-foreground">{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function NoTrial() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-start gap-3 px-4 py-16">
      <h1 className="text-base font-medium">No backtest to show for this idea</h1>
      <p className="text-muted-foreground">It hasn&apos;t been run yet, or its run has no step-by-step record. Ideas that have run open from the results map.</p>
      <a href="#research" className="hover:bg-raised inline-flex h-11 items-center rounded-md border px-4 text-sm font-medium sm:h-9">Open research</a>
    </div>
  );
}
