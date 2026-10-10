import { useMemo, useState } from "react";
import { AlertTriangle, ChevronRight, Pause } from "lucide-react";
import type { AppData } from "@/lib/types";
import { RANGES, comparison, lastChange, rangeStart, type RangeKey } from "@/lib/data";
import { stats } from "@/lib/stats";
import { clock, money, pct, pts, shortDate, signedMoney, tone, weekdayDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Page, RailHead, Section } from "@/components/Shell";
import { ReturnChart, type ChartEvent } from "@/components/ReturnChart";

const toneText = { gain: "text-gain", loss: "text-loss", flat: "text-muted-foreground" } as const;

/** Small trend line for a rail row, coloured like the number beside it. */
export function Spark({ values, t }: { values: number[]; t: "gain" | "loss" | "flat" }) {
  const lo = Math.min(...values), hi = Math.max(...values);
  const d = values.map((v, i) => `${i ? "L" : "M"}${((i / (values.length - 1)) * 64).toFixed(1)} ${(2 + (1 - (v - lo) / (hi - lo || 1)) * 20).toFixed(1)}`).join("");
  return <svg viewBox="0 0 64 24" className={cn("h-6 w-16 shrink-0", toneText[t])} aria-hidden><path d={d} fill="none" stroke="currentColor" strokeWidth="1.4" vectorEffect="non-scaling-stroke" /></svg>;
}

export function Pill({ v }: { v: number }) {
  const t = tone(v);
  return <span className={cn("num min-w-[76px] shrink-0 rounded-md px-2 py-1 text-center text-[13px]", t === "gain" ? "bg-gain/15 text-gain" : t === "loss" ? "bg-loss/15 text-loss" : "bg-secondary text-muted-foreground")}>{pct(v)}</span>;
}

const EVENTS: ChartEvent[] = [
  { date: "2026-08-31", label: "Monthly rebalance" },
  { date: "2026-09-01", label: "b3 started" },
  { date: "2026-09-30", label: "Monthly rebalance" },
  { date: "2026-10-09", label: "main started" },
];

/** Overview, "How am I doing?": one number, one chart against what the backtest expected, the statistics, and a side rail. */
export function Overview({ data }: { data: AppData }) {
  const [range, setRange] = useState<RangeKey>("ALL");
  const [hover, setHover] = useState<string | null>(null);

  const port = data.portfolio.equity;
  const today = lastChange(port)!;
  const last = port.at(-1)!;
  const own = useMemo(() => port.map((p) => ({ date: p.date, v: p.index })), [port]);
  const available = (k: RangeKey) => { const f = rangeStart(last.date, k); return !f || f >= own[0].date; };
  const cmp = useMemo(() => comparison(own, data.benchmark.closes, rangeStart(last.date, range)), [own, data.benchmark.closes, last.date, range]);
  const te = data.portfolio.expected_tracking_error;
  const band = useMemo(() => ({
    hi: cmp.spy.map((p, i) => ({ date: p.date, value: p.value + te * Math.sqrt(i / 252) })),
    lo: cmp.spy.map((p, i) => ({ date: p.date, value: p.value - te * Math.sqrt(i / 252) })),
  }), [cmp, te]);
  const st = useMemo(() => {
    const dates = new Set(cmp.you.map((p) => p.date));
    return stats(own.filter((p) => dates.has(p.date)).map((p) => p.v), data.benchmark.closes.filter((c) => dates.has(c.date)).map((c) => c.close));
  }, [cmp, own, data.benchmark.closes]);

  const at = hover ? cmp.you.findIndex((p) => p.date === hover) : -1;
  const i = at >= 0 ? at : cmp.you.length - 1;
  const youAt = cmp.you[i]?.value ?? 0, spyAt = cmp.spy[i]?.value ?? 0;
  const inBand = youAt <= band.hi[i].value && youAt >= band.lo[i].value;
  const event = EVENTS.find((e) => e.date === cmp.you[i]?.date);
  const gap = cmp.youRet - cmp.spyRet;
  const research = data.research.waiting;
  const h1 = data.research.ideas.find((x) => x.id === "h1");

  return (
    <Page rail={<Rail data={data} />}>
      {data.alerts.map((a) => (
        <div key={a.id} role="status" className="border-attention/40 bg-attention-soft mb-5 flex items-start gap-3 rounded-lg border p-4">
          <AlertTriangle className="text-attention mt-0.5 size-4 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="font-medium">{a.title}</p>
            <p className="text-muted-foreground mt-0.5">{a.todo}</p>
          </div>
        </div>
      ))}

      <p className="text-muted-foreground">All books</p>
      <p className="num mt-0.5 text-[34px] font-medium tracking-[-0.03em] sm:text-[40px]">{money(last.value)}</p>
      <p className={cn("num", toneText[tone(today.rel)])}>{signedMoney(today.abs)} ({pct(today.rel)}) <span className="text-muted-foreground font-sans">{weekdayDate(last.date).split(",")[0]}</span></p>

      <div className="mt-6">
        <ReturnChart
          you={cmp.you}
          spy={cmp.spy}
          band={band}
          events={EVENTS}
          tone={tone(cmp.youRet)}
          onScrub={setHover}
          height={typeof window !== "undefined" && window.innerWidth < 640 ? 230 : 320}
          label={`Your return ${pct(cmp.youRet)} against the S&P 500 ${pct(cmp.spyRet)}, with the backtest's expected range.`}
        />
      </div>
      <p className="num text-muted-foreground mt-2 flex min-h-5 flex-wrap gap-x-5 gap-y-1 text-xs">
        <span className={toneText[tone(youAt)]}>You {pct(youAt)}</span>
        <span>S&amp;P 500 {pct(spyAt)}</span>
        <span>{inBand ? "inside" : "outside"} the expected range</span>
        <span>{shortDate(cmp.you[0]?.date ?? last.date)} – {shortDate(cmp.you[i]?.date ?? last.date)}</span>
        {event && <span className="text-foreground">{event.label}</span>}
      </p>

      <div role="radiogroup" aria-label="Time range" className="mt-3 flex gap-1 border-b pb-4">
        {RANGES.map((r) => {
          const ok = available(r.key), on = r.key === range;
          return (
            <button key={r.key} role="radio" aria-checked={on} disabled={!ok} title={ok ? undefined : "Not enough history yet"} onClick={() => setRange(r.key)}
              className={cn("h-9 min-w-11 rounded-full px-3 text-[13px] font-medium transition-colors", on ? (tone(cmp.youRet) === "loss" ? "bg-loss/15 text-loss" : "bg-gain/15 text-gain") : "text-muted-foreground hover:text-foreground", !ok && "opacity-35")}>
              {r.label}
            </button>
          );
        })}
      </div>

      <Section title="Statistics" note={`${shortDate(cmp.you[0]?.date ?? last.date)} to ${shortDate(last.date)}, ${st.days} trading days`}>
        <dl className="num grid grid-cols-2 border-t sm:grid-cols-4">
          <Stat label="Excess vs S&P"><span className={toneText[tone(gap)]}>{gap >= 0 ? "+" : "−"}{pts(gap)}</span></Stat>
          <Stat label="Luck check, H1">{h1?.result ? `${Math.round(h1.result.luck * 100)}%` : "—"}</Stat>
          <Stat label="Volatility, yearly">{(st.vol * 100).toFixed(1)}%</Stat>
          <Stat label="Max drawdown"><span className="text-loss">{pct(st.maxDD, 1)}</span></Stat>
          <Stat label="Sharpe, yearly">{st.sharpe.toFixed(2)}</Stat>
          <Stat label="Beta to S&P">{st.beta.toFixed(2)}</Stat>
          <Stat label="Tracking error">{(st.te * 100).toFixed(1)}%</Stat>
          <Stat label="Expected range">{inBand ? <span className="text-gain">Inside</span> : <span className="text-attention">Outside</span>}</Stat>
        </dl>
        <p className="text-muted-foreground mt-3 max-w-[70ch] text-xs">
          Daily closes after costs, time-weighted so new money is not a gain. Sharpe uses a zero risk-free rate. Under about 60 trading days is too short to judge skill; the grey band is what the backtests said to expect.
        </p>
      </Section>

      {research.length > 0 && (
        <Section title="Needs you" note={<span className="num text-attention">{research.length}</span>}>
          <a href="#research" className="bg-raised hover:border-ring flex items-center gap-4 rounded-lg border p-4 transition-colors">
            <span className="min-w-0 flex-1">
              <span className="block font-medium">{research[0].title}</span>
              <span className="text-muted-foreground block text-[13px]">{research[0].why}</span>
            </span>
            <ChevronRight className="text-muted-foreground size-4 shrink-0" />
          </a>
          {research.length > 1 && <a href="#research" className="text-muted-foreground hover:text-foreground mt-2.5 inline-flex items-center gap-1 text-[13px]">{research.length - 1} more research decisions<ChevronRight className="size-3.5" /></a>}
        </Section>
      )}
    </Page>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="border-b py-3">
      <dt className="text-muted-foreground font-sans text-[12.5px]">{label}</dt>
      <dd className="mt-1 text-base">{children}</dd>
    </div>
  );
}

function Rail({ data }: { data: AppData }) {
  const onPaper = data.research.ideas.filter((x) => x.stage === "on_paper");
  return (
    <>
      <RailHead>Books</RailHead>
      {data.books.map((b) => {
        const c = comparison(b.equity.map((p) => ({ date: p.date, v: p.value })), data.benchmark.closes, null);
        const stopped = b.status.state === "stopped";
        return (
          <a key={b.id} href={`#books/${b.id}`} className="hover:bg-raised -mx-2 flex items-center gap-3 rounded-md border-b px-2 py-3 last-of-type:border-0">
            <span className="min-w-0 flex-1">
              <span className="block font-medium">{b.name}</span>
              <span className="text-muted-foreground block truncate text-xs">
                {stopped ? <span className="text-attention inline-flex items-center gap-1"><Pause className="size-3" />Stopped by you {clock(b.status.at!)}</span> : data.strategies.find((s) => s.id === b.strategy_id)?.name}
              </span>
            </span>
            {b.equity.length > 4 ? <Spark values={b.equity.map((p) => p.value)} t={tone(c.youRet)} /> : <span className="text-muted-foreground w-16 text-center text-xs">day {b.equity.length}</span>}
            <Pill v={c.youRet} />
          </a>
        );
      })}

      <RailHead>On paper, taking their exam</RailHead>
      {onPaper.map((x) => (
        <a key={x.id} href="#research" className="hover:bg-raised -mx-2 flex items-center gap-3 rounded-md border-b px-2 py-3 last-of-type:border-0">
          <span className="min-w-0 flex-1">
            <span className="block font-medium">{x.name}</span>
            <span className="num text-muted-foreground block text-xs">{x.exam?.kind === "paper" ? `${x.exam.done} of ${x.exam.of} ${x.exam.unit}` : "exam on hold"}</span>
          </span>
          <span className="text-right">
            <span className="num block">{x.result ? `${Math.round(x.result.luck * 100)}%` : "—"}</span>
            <span className="text-muted-foreground block text-xs">luck check</span>
          </span>
        </a>
      ))}
    </>
  );
}
