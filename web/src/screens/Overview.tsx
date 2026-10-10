import { useMemo, useState } from "react";
import { AlertTriangle, ChevronRight, Pause } from "lucide-react";
import type { AppData, Book } from "@/lib/types";
import { RANGES, comparison, lastChange, rangeStart, type RangeKey } from "@/lib/data";
import { clock, money, pct, pts, shortDate, signedMoney, tone, weekdayDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Panel } from "@/components/Shell";
import { ReturnChart } from "@/components/ReturnChart";

const toneText = { gain: "text-gain", loss: "text-loss", flat: "text-muted-foreground" } as const;
const signedPts = (g: number) => `${g >= 0 ? "+" : "−"}${pts(g)}`;

/** One labelled figure in the quote strip. */
function Quote({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bg-background flex flex-col gap-0.5 px-4 py-2.5 sm:flex-row sm:items-baseline sm:gap-2">
      <span className="text-muted-foreground text-xs">{label}</span>
      <span className="num text-[13px]">{children}</span>
    </div>
  );
}

/** Overview, "How am I doing?": a quote strip, the chart as the hero, books and anything waiting beside it. */
export function Overview({ data }: { data: AppData }) {
  const [range, setRange] = useState<RangeKey>("ALL");
  const [hover, setHover] = useState<string | null>(null);

  const port = data.portfolio.equity;
  const today = lastChange(port)!;
  const total = port.at(-1)!.value;
  const lastDate = port.at(-1)!.date;
  const own = useMemo(() => port.map((p) => ({ date: p.date, v: p.index })), [port]);
  const available = (k: RangeKey) => { const f = rangeStart(lastDate, k); return !f || f >= own[0].date; };
  const all = useMemo(() => comparison(own, data.benchmark.closes, null), [own, data.benchmark.closes]);
  const cmp = useMemo(() => comparison(own, data.benchmark.closes, rangeStart(lastDate, range)), [own, data.benchmark.closes, lastDate, range]);
  const at = hover ? cmp.you.findIndex((p) => p.date === hover) : -1;
  const youAt = at >= 0 ? cmp.you[at].value : cmp.youRet;
  const spyAt = at >= 0 ? cmp.spy[at].value : cmp.spyRet;
  const from = cmp.you[0]?.date ?? lastDate;
  const to = at >= 0 ? cmp.you[at].date : lastDate;
  const running = data.books.filter((b) => b.status.state === "running").sort((a, b) => a.next_run.at.localeCompare(b.next_run.at));
  const research = data.research.waiting;
  const needs = data.alerts.length + research.length;

  return (
    <div className="flex min-h-[calc(100dvh-2.75rem)] flex-col">
      {data.alerts.map((a) => {
        const book = data.books.find((b) => b.id === a.book_id);
        return (
          <div key={a.id} role="status" className="bg-attention-soft flex flex-wrap items-start gap-x-4 gap-y-1 border-b px-4 py-3">
            <AlertTriangle className="text-attention mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="text-attention font-medium">{a.title}</p>
              <p className="text-muted-foreground mt-0.5">{a.detail} <span className="text-foreground">{a.todo}</span></p>
            </div>
            {book && <a href={`#books/${book.id}`} className="text-foreground inline-flex items-center gap-1 underline-offset-4 hover:underline">Open {book.name}<ChevronRight className="size-3.5" /></a>}
          </div>
        );
      })}

      <div className="bg-border grid grid-cols-2 gap-px border-b sm:flex sm:[&>*]:flex-none">
        <Quote label="Total">{money(total)} <span className={toneText[tone(today.rel)]}>{pct(today.rel)}</span></Quote>
        <Quote label={`Since ${shortDate(own[0].date)}`}><span className={toneText[tone(all.youRet)]}>{pct(all.youRet)}</span></Quote>
        <Quote label="S&P 500"><span className={toneText[tone(all.spyRet)]}>{pct(all.spyRet)}</span></Quote>
        <Quote label="Needs you"><span className={needs ? "text-attention" : ""}>{needs}</span></Quote>
        <div className="bg-background hidden sm:block sm:flex-1!" />
      </div>

      <div className="bg-border grid flex-1 gap-px lg:grid-cols-[minmax(0,1.7fr)_minmax(340px,1fr)] lg:grid-rows-[auto_1fr]">
        <Panel
          className="lg:row-span-2"
          title="Return vs S&P 500"
          aside={
            <div role="radiogroup" aria-label="Time range" className="flex gap-0.5">
              {RANGES.map((r) => {
                const ok = available(r.key);
                return (
                  <button
                    key={r.key}
                    role="radio"
                    aria-checked={r.key === range}
                    disabled={!ok}
                    title={ok ? undefined : "Not enough history yet"}
                    onClick={() => setRange(r.key)}
                    className={cn(
                      "num h-7 min-w-9 rounded px-2 text-xs transition-colors max-sm:h-10 max-sm:min-w-11",
                      r.key === range ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
                      !ok && "opacity-35",
                    )}
                  >
                    {r.label}
                  </button>
                );
              })}
            </div>
          }
        >
          <ReturnChart
            you={cmp.you}
            spy={cmp.spy}
            tone={tone(cmp.youRet)}
            onScrub={setHover}
            height={typeof window !== "undefined" && window.innerWidth < 640 ? 300 : 460}
            label={`Your return ${pct(cmp.youRet)} against the S&P 500 ${pct(cmp.spyRet)}, ${shortDate(from)} to ${shortDate(lastDate)}.`}
          />
          <p className="num text-muted-foreground mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs">
            <span className={toneText[tone(youAt)]}>You {pct(youAt)}</span>
            <span>S&amp;P 500 {pct(spyAt)}</span>
            <span>{tone(youAt - spyAt) === "flat" ? "level" : `${pts(youAt - spyAt)} ${youAt > spyAt ? "ahead" : "behind"}`}</span>
            <span>{shortDate(from)} – {shortDate(to)}</span>
            <span className={toneText[tone(today.rel)]}>{signedMoney(today.abs)} {weekdayDate(lastDate).split(",")[0]}</span>
          </p>
        </Panel>

        <Panel title="Books" aside={`${running.length} running`}>
          <table className="w-full">
            <thead className="text-muted-foreground text-xs">
              <tr className="border-b">
                <th className="py-1.5 text-left font-normal">Book</th>
                <th className="py-1.5 text-right font-normal">Value</th>
                <th className="py-1.5 text-right font-normal">Return</th>
                <th className="py-1.5 text-right font-normal">vs S&amp;P</th>
              </tr>
            </thead>
            <tbody>{data.books.map((b) => <BookRow key={b.id} book={b} data={data} />)}</tbody>
          </table>
        </Panel>

        <Panel title="Needs you" aside={needs ? <span className="num text-attention">{needs}</span> : undefined}>
          {data.alerts.length === 0 && <p className="text-muted-foreground border-b pb-2">Books: every run went as planned. Next: {running[0]?.name}, {weekdayDate(running[0]?.next_run.at ?? data.as_of).split(",")[0]} {running[0] && clock(running[0].next_run.at)}.</p>}
          {research.length > 0 && <p className="text-muted-foreground pt-2 text-xs">Research decisions</p>}
          <ul>
            {research.slice(0, 4).map((w) => (
              <li key={w.id} className="border-b last:border-0">
                <a href="#research" className="hover:bg-raised -mx-2 flex items-baseline justify-between gap-3 rounded px-2 py-2">
                  <span>{w.title}</span>
                  <span className="text-muted-foreground shrink-0 text-xs">{w.effort}</span>
                </a>
              </li>
            ))}
          </ul>
          {research.length > 4 && <a href="#research" className="text-muted-foreground hover:text-foreground mt-1 inline-block text-xs">{research.length - 4} more in Research</a>}
        </Panel>
      </div>
    </div>
  );
}

function BookRow({ book, data }: { book: Book; data: AppData }) {
  const strat = data.strategies.find((s) => s.id === book.strategy_id);
  const cmp = comparison(book.equity.map((p) => ({ date: p.date, v: p.value })), data.benchmark.closes, null);
  const young = book.equity.length < 5;
  const gap = cmp.youRet - cmp.spyRet;
  const flagged = data.alerts.some((a) => a.book_id === book.id);
  const stopped = book.status.state === "stopped";
  return (
    <tr className="hover:bg-raised cursor-pointer border-b last:border-0" onClick={() => (location.hash = `books/${book.id}`)}>
      <td className="py-2 pr-2 align-top">
        <a href={`#books/${book.id}`} className="font-medium hover:underline">{book.name}</a>
        <div className="text-muted-foreground text-[11px]">
          {stopped ? <span className="text-attention inline-flex items-center gap-1"><Pause className="size-3" />Stopped by you {clock(book.status.at!)}</span>
            : flagged ? <span className="text-attention inline-flex items-center gap-1"><AlertTriangle className="size-3" />Needs you</span>
            : strat?.name}
        </div>
      </td>
      <td className="num py-2 text-right align-top">{money(book.equity.at(-1)!.value)}</td>
      <td className={cn("num py-2 text-right align-top", toneText[tone(cmp.youRet)])}>{pct(cmp.youRet)}</td>
      <td className={cn("num py-2 text-right align-top", young ? "text-muted-foreground" : toneText[tone(gap)])}>{young ? `day ${book.equity.length}` : signedPts(gap)}</td>
    </tr>
  );
}
