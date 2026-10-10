import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, Info } from "lucide-react";
import type { AppData, Book, BookStatus, Order, Position, Rule } from "@/lib/types";
import { comparison, rangeStart, type RangeKey } from "@/lib/data";
import { clock, money, pct, shortDate, signedMoney, tone, weekdayDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Page, RailHead, Section } from "@/components/Shell";
import { ReturnChart, type ChartEvent } from "@/components/ReturnChart";
import { RangePicker } from "@/components/RangePicker";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Pill, Spark } from "@/screens/Overview";

const toneText = { gain: "text-gain", loss: "text-loss", flat: "text-muted-foreground" } as const;
const pctW = (v: number) => `${(v * 100).toFixed(1)}%`;
const yearDate = (iso: string) => `${shortDate(iso)}, ${iso.slice(0, 4)}`;
const at = (iso: string) => `${weekdayDate(iso)} at ${clock(iso)}`;
// Prototype only: ?state=stopfail makes the stop and resume calls fail once.
const failOnce = { left: new URLSearchParams(location.search).get("state") === "stopfail" };

function useBookId(): string | null {
  const read = () => location.hash.replace("#", "").split("/")[1] ?? null;
  const [id, setId] = useState(read);
  useEffect(() => {
    const on = () => setId(read());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return id;
}

/** Books: one book in detail. With no book in the address, the one with the longest record. */
export function Books({ data }: { data: AppData }) {
  const id = useBookId();
  // Stops and resumes made here; the real app would post them to the local API.
  const [status, setStatus] = useState<Record<string, BookStatus>>({});
  const fallback = [...data.books].sort((a, b) => b.equity.length - a.equity.length)[0];
  const book = id ? data.books.find((b) => b.id === id) : fallback;
  if (!book) return <BookNotFound id={id ?? ""} first={fallback?.id} />;
  const live = { ...book, status: status[book.id] ?? book.status };
  return <BookDetail key={book.id} data={data} book={live} onStatus={(s) => setStatus((m) => ({ ...m, [book.id]: s }))} />;
}

type Open = { kind: "holding"; p: Position } | { kind: "order"; o: Order } | null;

function BookDetail({ data, book, onStatus }: { data: AppData; book: Book; onStatus: (s: BookStatus) => void }) {
  const strategy = data.strategies.find((s) => s.id === book.strategy_id)!;
  const idea = data.research.ideas.find((i) => i.book_id === book.id);
  const [range, setRange] = useState<RangeKey>("ALL");
  const [hover, setHover] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"stop" | "resume" | null>(null);
  const [open, setOpen] = useState<Open>(null);

  const last = book.equity.at(-1)!;
  const sinceStart = last.value / book.start_equity - 1;
  const own = useMemo(() => book.equity.map((p) => ({ date: p.date, v: p.value })), [book.equity]);
  const available = (k: RangeKey) => { const f = rangeStart(last.date, k); return !f || f >= own[0].date; };
  const cmp = useMemo(() => comparison(own, data.benchmark.closes, rangeStart(last.date, range)), [own, data.benchmark.closes, last.date, range]);
  const te = book.expected_tracking_error;
  const band = useMemo(() => ({
    hi: cmp.spy.map((p, i) => ({ date: p.date, value: p.value + te * Math.sqrt(i / 252) })),
    lo: cmp.spy.map((p, i) => ({ date: p.date, value: p.value - te * Math.sqrt(i / 252) })),
  }), [cmp, te]);
  const events = useMemo<ChartEvent[]>(() => {
    const byDay = new Map<string, number>();
    for (const o of book.orders) if (o.filled_shares > 0) { const d = o.placed_at.slice(0, 10); byDay.set(d, (byDay.get(d) ?? 0) + 1); }
    return [...byDay].map(([date, n]) => ({ date, label: `${n} order${n > 1 ? "s" : ""} filled` }));
  }, [book.orders]);

  const i = Math.max(0, hover ? cmp.you.findIndex((p) => p.date === hover) : cmp.you.length - 1);
  const youAt = cmp.you[i]?.value ?? 0, spyAt = cmp.spy[i]?.value ?? 0;
  const where = cmp.you.length === 0 ? "inside" : youAt > band.hi[i].value ? "above" : youAt < band.lo[i].value ? "below" : "inside";
  const event = events.find((e) => e.date === cmp.you[i]?.date);
  const stopped = book.status.state === "stopped";
  const hasHistory = cmp.you.length > 1;

  return (
    <Page rail={<Rail data={data} book={book} stopped={stopped} />}>
      <BookSwitcher data={data} current={book.id} />

      <div className="flex items-start gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-muted-foreground max-lg:sr-only">{book.name}</h1>
          <p className="num mt-0.5 text-[34px] font-medium tracking-[-0.03em] sm:text-[40px]">{money(last.value)}</p>
          <p className={cn("num", toneText[tone(sinceStart)])}>
            {signedMoney(last.value - book.start_equity)} ({pct(sinceStart)}) <span className="text-muted-foreground font-sans">since {shortDate(book.started_on)}</span>
          </p>
        </div>
        {!stopped && <Button variant="outline" className="mt-1 h-11 sm:h-9" onClick={() => setDialog("stop")}>Stop book</Button>}
      </div>

      {stopped && <StoppedNotice book={book} onResume={() => setDialog("resume")} />}

      <div className="mt-6">
        {hasHistory ? (
          <ReturnChart
            you={cmp.you}
            spy={cmp.spy}
            band={band}
            events={events}
            tone={tone(cmp.youRet)}
            onScrub={setHover}
            height={typeof window !== "undefined" && window.innerWidth < 640 ? 230 : 300}
            label={`${book.name} return ${pct(cmp.youRet)} against the S&P 500 ${pct(cmp.spyRet)}, with the backtest's expected range.`}
          />
        ) : (
          <div className="text-muted-foreground grid h-40 place-items-center rounded-lg border border-dashed px-6 text-center">
            <p className="max-w-sm">The chart starts after its first full trading day. It opened {weekdayDate(book.started_on)} with {money(book.start_equity)}.</p>
          </div>
        )}
      </div>
      {hasHistory && (
        <>
          <p className="num text-muted-foreground mt-2 flex min-h-5 flex-wrap items-center gap-x-5 gap-y-1 text-xs">
            <span className={toneText[tone(youAt)]}>{book.name} {pct(youAt)}</span>
            <span>S&amp;P 500 {pct(spyAt)}</span>
            <span className="inline-flex items-center gap-1.5">{where} the expected range <RangeInfo te={te} period={strategy.backtest.period} /></span>
            {hover && <span>{shortDate(cmp.you[i].date)}</span>}
            {hover && event && <span className="text-foreground">{event.label}</span>}
          </p>
          <RangePicker value={range} onChange={setRange} available={available} tone={tone(cmp.youRet)} />
        </>
      )}

      <Holdings book={book} rule={strategy.rule} onOpen={(p) => setOpen({ kind: "holding", p })} />
      <WhyItHolds book={book} rule={strategy.rule} />
      <Orders book={book} onOpen={(o) => setOpen({ kind: "order", o })} />

      <p className="text-muted-foreground mt-10 border-t pt-4 text-xs">
        Book id {book.id}, strategy {strategy.id}{idea && <>, research idea {idea.code}</>}. Sample data.
      </p>

      <StopResumeDialog book={book} mode={dialog} onClose={() => setDialog(null)} onDone={(s) => { onStatus(s); setDialog(null); }} />
      <DetailSheet open={open} book={book} rule={strategy.rule} onClose={() => setOpen(null)} />
    </Page>
  );
}

/** Phone only: the books as a row of pills, since the rail with the full list sits at the bottom there. */
function BookSwitcher({ data, current }: { data: AppData; current: string }) {
  return (
    <nav aria-label="Books" className="-mx-1 mb-5 flex gap-1 lg:hidden">
      {data.books.map((b) => (
        <a key={b.id} href={`#books/${b.id}`} aria-current={b.id === current ? "page" : undefined}
          className={cn("inline-flex h-11 min-w-11 items-center rounded-full px-4 text-[13px] font-medium", b.id === current ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground")}>
          {b.name}{b.status.state === "stopped" && <span className="text-attention ml-1.5 font-normal">stopped</span>}
        </a>
      ))}
    </nav>
  );
}

/** The one boxed item on the screen: the book is stopped, by whom, when, why, and the way back. */
function StoppedNotice({ book, onResume }: { book: Book; onResume: () => void }) {
  const s = book.status;
  const who = s.by === "safety" ? `the ${s.rule?.toLowerCase() ?? "safety rule"}` : "you";
  return (
    <div role="status" className="border-attention/40 bg-attention-soft mt-5 flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-start">
      <div className="min-w-0 flex-1">
        <p className="font-medium"><span className="text-attention">Stopped</span> by {who}, {at(s.at!)}</p>
        {s.reason && <p className="mt-1">{s.by === "safety" ? s.reason : <>“{s.reason}”</>}</p>}
        <p className="text-muted-foreground mt-1 text-[13px]">
          No orders go out until you resume it. It still holds {book.positions.length ? `its ${book.positions.length} stocks` : "its cash"}, and the value keeps updating.
        </p>
      </div>
      <Button className="h-11 shrink-0 sm:h-9" onClick={onResume}>Resume…</Button>
    </div>
  );
}

function RangeInfo({ te, period }: { te: number; period: string }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button aria-label="How the expected range is drawn" className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 -m-2 inline-grid size-8 place-items-center rounded-full outline-none focus-visible:ring-2">
          <Info className="size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent collisionPadding={12} className="w-[min(92vw,360px)] font-sans text-[13px] leading-relaxed" align="start">
        <p className="font-medium">How the expected range is drawn</p>
        <p className="text-muted-foreground mt-1">
          The grey band is where the backtest says this book should land relative to the S&amp;P 500 about two days in three.
        </p>
        <p className="num mt-3 text-xs">band = S&amp;P return ± {pctW(te)} × √(days ÷ 252)</p>
        <p className="text-muted-foreground mt-2 text-xs">
          {pctW(te)} is the yearly tracking error, the spread of the backtest's daily returns over the S&amp;P ({yearDate(period.slice(0, 10))} to {yearDate(period.slice(-10))}). It widens with the square root of time,
          so a short stretch outside it says little. This value is sample data until the backtest supplies it.
        </p>
      </PopoverContent>
    </Popover>
  );
}

const ROWS = 10;
/** How many names the rule holds: the top fraction of the names scored. A book with holdings shows its own count. */
const holdOf = (book: Book, rule: Rule) => book.positions.length || Math.round(rule.universe * rule.top_fraction);

function Holdings({ book, rule, onOpen }: { book: Book; rule: Rule; onOpen: (p: Position) => void }) {
  const [all, setAll] = useState(false);
  const total = book.equity.at(-1)!.value;
  const shown = all ? book.positions : book.positions.slice(0, ROWS);
  const grid = "grid grid-cols-[minmax(0,1fr)_64px_16px] sm:grid-cols-[minmax(0,1fr)_88px_110px_72px_96px_16px] items-center gap-x-3";
  return (
    <Section title="Holdings" note={book.positions.length ? `${book.positions.length} stocks at equal weight, and cash` : undefined}>
      {book.positions.length === 0 ? (
        <p className="text-muted-foreground border-t pt-3">
          Nothing yet; all {money(book.cash)} is in cash. {book.next_run.what} {weekdayDate(book.next_run.at!)} at {clock(book.next_run.at!)}.
        </p>
      ) : (
        <>
          <div className={cn(grid, "text-muted-foreground border-b pb-2 text-xs")}>
            <span>Stock</span>
            <span className="hidden text-right sm:block" title="Rank at the last rebalance">Rank</span>
            <span className="hidden text-right sm:block">Value</span>
            <span className="text-right">Weight</span>
            <span className="hidden text-right sm:block">Gain</span>
            <span />
          </div>
          <ul>
            {shown.map((p) => {
              return (
                <li key={p.symbol} className="border-b">
                  <button onClick={() => onOpen(p)} className={cn(grid, "hover:bg-raised focus-visible:ring-ring/50 -mx-2 min-h-12 w-[calc(100%+1rem)] rounded-md px-2 py-2 text-left outline-none focus-visible:ring-2")}>
                    <span className="flex min-w-0 items-baseline gap-2.5">
                      <span className="num w-12 shrink-0 font-medium">{p.symbol}</span>
                      <span className="text-muted-foreground truncate text-[13px]">{p.name}</span>
                    </span>
                    <span className={cn("num hidden text-right sm:block", p.rank > holdOf(book, rule) && "text-attention")}>{p.rank}</span>
                    <span className="num hidden text-right sm:block">{money(p.value)}</span>
                    <span className="num text-right">{pctW(p.weight)}</span>
                    <span className={cn("num hidden text-right sm:block", toneText[tone(p.unrealized_pnl)])}>{signedMoney(Math.round(p.unrealized_pnl)).replace(".00", "")}</span>
                    <ChevronRight className="text-muted-foreground size-4" />
                  </button>
                </li>
              );
            })}
            <li className={cn(grid, "min-h-12 border-b py-2")}>
              <span className="text-muted-foreground">Cash</span>
              <span className="hidden sm:block" />
              <span className="num hidden text-right sm:block">{money(book.cash)}</span>
              <span className="num text-right">{pctW(book.cash / total)}</span>
            </li>
          </ul>
          {book.positions.length > ROWS && (
            <button onClick={() => setAll(!all)} className="text-muted-foreground hover:text-foreground mt-2 h-11 text-[13px]">
              {all ? `Show the top ${ROWS}` : `Show all ${book.positions.length}`}
            </button>
          )}
          <p className="text-muted-foreground mt-2 max-w-[70ch] text-xs">
            Weights drift as prices move; every rebalance trades each holding back to an equal share.
          </p>
        </>
      )}
    </Section>
  );
}

/**
 * Why it holds what it holds, as the engine decides it: rank the universe on
 * the measure, hold the top fraction at equal weight. The picture is a rank
 * scale with the cut drawn on it: a tick per holding at its rank today, a
 * hollow mark for each recent sale at the rank it had fallen to.
 */
function WhyItHolds({ book, rule }: { book: Book; rule: Rule }) {
  const cut = holdOf(book, rule);
  const max = Math.round(cut * 1.5);
  const x = (rank: number) => ((Math.min(rank, max) - 0.5) / max) * 100;
  const sold = book.orders
    .filter((o) => o.side === "sell" && o.status === "filled" && /rank (\d+)/.test(o.why))
    .map((o) => ({ symbol: o.symbol, rank: Number(/rank (\d+)/.exec(o.why)![1]), date: o.placed_at }));
  const slipped = book.positions.filter((p) => p.rank > cut); // empty by the rule: ranks are the last rebalance's
  const lastRebalance = book.rebalances[0]?.date;
  const share = `${Math.round(rule.top_fraction * 100)}%`;
  return (
    <Section title={book.positions.length ? "Why it holds these" : "How it will choose"}>
      <p className="max-w-[70ch]">
        {rule.check.charAt(0).toUpperCase() + rule.check.slice(1)}, it ranks the {rule.universe.toLocaleString("en-US")} largest US stocks by {rule.measure} and
        holds the top {share}, about {cut} names, at equal weight. A name that drops out of the top {share} is sold at the next rebalance; one that enters is bought.
      </p>

      <div className="mt-5" role="img" aria-label={`Rank scale: ${book.positions.length} holdings, ${slipped.length} below the cut at rank ${cut}. Recently sold: ${sold.map((s) => `${s.symbol} at rank ${s.rank}`).join(", ") || "none"}.`}>
        <div className="relative h-8 overflow-hidden rounded-md border">
          <div className="bg-foreground/[0.06] absolute inset-y-0 left-0 border-r" style={{ width: `${(cut / max) * 100}%` }} />
          {book.positions.map((p) => (
            <span key={p.symbol} title={`${p.symbol}, rank ${p.rank}`} className={cn("absolute inset-y-1.5 w-px", p.rank > cut ? "bg-attention" : "bg-foreground/70")} style={{ left: `${x(p.rank)}%` }} />
          ))}
          {sold.map((s) => (
            <span key={s.symbol + s.date} title={`${s.symbol}, sold at rank ${s.rank}`} className="border-muted-foreground absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-[1.5px]" style={{ left: `${x(s.rank)}%` }} />
          ))}
        </div>
        <div className="text-muted-foreground relative mt-1.5 h-4 text-[11px]">
          <span className="absolute left-0">Held: rank 1 to {cut}</span>
          <span className="absolute pl-1.5" style={{ left: `${(cut / max) * 100}%` }}>Not held</span>
        </div>
      </div>

      <dl className="mt-4 grid gap-x-8 gap-y-3 text-[13px] sm:grid-cols-2">
        {book.positions.length > 0 && (
          <div>
            <dt className="text-muted-foreground">Ranks are from the last rebalance</dt>
            <dd className="mt-0.5">{lastRebalance ? weekdayDate(lastRebalance) : "none yet"}; the next ranks the stocks again {weekdayDate(book.next_run.at!)}</dd>
          </div>
        )}
        <div>
          <dt className="text-muted-foreground">Sold lately, at rank</dt>
          <dd className="num mt-0.5">{sold.length ? sold.slice(0, 6).map((s) => `${s.symbol} ${s.rank}`).join(", ") : "none yet"}</dd>
        </div>
      </dl>
    </Section>
  );
}

const verb = (o: Order) => (o.filled_shares > 0 ? (o.side === "buy" ? "Bought" : "Sold") : o.side === "buy" ? "Buy" : "Sell");
const STATUS: Record<Order["status"], string> = { filled: "Filled", partial: "Part filled", open: "Open", cancelled: "Not filled", rejected: "Rejected" };

function Orders({ book, onOpen }: { book: Book; onOpen: (o: Order) => void }) {
  const [all, setAll] = useState(false);
  const shown = all ? book.orders : book.orders.slice(0, 6);
  const days = [...new Set(shown.map((o) => o.placed_at.slice(0, 10)))];
  return (
    <Section title="Recent orders">
      {book.orders.length === 0 ? (
        <p className="text-muted-foreground border-t pt-3">No orders yet. The first go out {weekdayDate(book.next_run.at!)} at {clock(book.next_run.at!)}.</p>
      ) : (
        <>
          {days.map((d) => (
            <div key={d} className="mt-3 first-of-type:mt-0">
              <h3 className="text-muted-foreground border-b pb-1.5 text-xs">{weekdayDate(d)}</h3>
              {(() => {
                const rb = book.rebalances.find((x) => x.date === d);
                return rb ? (
                  <p className="text-muted-foreground flex min-h-11 items-center gap-3 border-b py-2 text-[13px]">
                    <span>Traded {rb.reweighted} holdings back to equal weight{rb.skipped_dust > 0 && <>; skipped {rb.skipped_dust} too small to place</>}</span>
                  </p>
                ) : null;
              })()}
              <ul>
                {shown.filter((o) => o.placed_at.startsWith(d)).map((o) => {
                  const bad = o.status === "cancelled" || o.status === "rejected";
                  return (
                    <li key={o.id} className="border-b">
                      <button onClick={() => onOpen(o)} className="hover:bg-raised focus-visible:ring-ring/50 -mx-2 grid min-h-12 w-[calc(100%+1rem)] grid-cols-[minmax(0,1fr)_auto_16px] items-center gap-x-3 rounded-md px-2 py-2 text-left outline-none focus-visible:ring-2 sm:grid-cols-[180px_minmax(0,1fr)_auto_16px]">
                        <span className="truncate">{verb(o)} <span className="num">{o.shares}</span> <span className="num font-medium">{o.symbol}</span></span>
                        <span className="text-muted-foreground hidden truncate text-[13px] sm:block">{o.why}</span>
                        <span className={cn("num text-right text-[13px]", bad ? "text-attention font-sans" : "")}>{bad ? STATUS[o.status] : o.fill_price != null ? money(o.fill_price) : STATUS[o.status]}</span>
                        <ChevronRight className="text-muted-foreground size-4" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
          {book.orders.length > 6 && (
            <button onClick={() => setAll(!all)} className="text-muted-foreground hover:text-foreground mt-2 h-11 text-[13px]">{all ? "Show fewer" : `Show all ${book.orders.length}`}</button>
          )}
        </>
      )}
    </Section>
  );
}

function Rail({ data, book, stopped }: { data: AppData; book: Book; stopped: boolean }) {
  const s = data.strategies.find((x) => x.id === book.strategy_id)!;
  const bt = s.backtest;
  const idea = data.research.ideas.find((i) => i.book_id === book.id);
  return (
    <div className="mt-3 lg:mt-0">
      <div className="hidden lg:block">
      <RailHead>Books</RailHead>
      {data.books.map((b) => {
        const c = comparison(b.equity.map((p) => ({ date: p.date, v: p.value })), data.benchmark.closes, null);
        const here = b.id === book.id;
        return (
          <a key={b.id} href={`#books/${b.id}`} aria-current={here ? "page" : undefined}
            className={cn("hover:bg-raised -mx-2 flex items-center gap-3 rounded-md border-b px-2 py-3 last-of-type:border-0", here && "bg-raised")}>
            <span className="min-w-0 flex-1">
              <span className="block font-medium">{b.name}</span>
              <span className="text-muted-foreground block truncate text-xs">
                {(here ? book.status.state : b.status.state) === "stopped" ? <span className="text-attention">Stopped</span> : data.strategies.find((x) => x.id === b.strategy_id)?.name}
              </span>
            </span>
            {b.equity.length > 4 ? <Spark values={b.equity.map((p) => p.value)} t={tone(c.youRet)} /> : <span className="text-muted-foreground w-16 text-center text-xs">day {b.equity.length}</span>}
            <Pill v={c.youRet} />
          </a>
        );
      })}
      </div>

      <RailHead>Schedule</RailHead>
      <dl className="text-[13px]">
        <div className="border-b py-3">
          <dt className="text-muted-foreground text-xs">Last run, {at(book.last_run.at)}</dt>
          <dd className="mt-0.5">{book.last_run.summary}</dd>
        </div>
        <div className="border-b py-3">
          <dt className="text-muted-foreground text-xs">Next run</dt>
          <dd className="mt-0.5">{stopped ? <span className="text-attention">Skipped while stopped</span> : <>{book.next_run.what}, {at(book.next_run.at!)}</>}</dd>
        </div>
        <div className="py-3">
          <dt className="text-muted-foreground text-xs">Started</dt>
          <dd className="mt-0.5">{weekdayDate(book.started_on)} with <span className="num">{money(book.start_equity)}</span></dd>
        </div>
      </dl>

      <RailHead>What the backtest said</RailHead>
      <Rows rows={[
        ["Return a year", pctW(bt.annual_return)],
        ["S&P 500 a year", pctW(bt.benchmark_annual_return)],
        ["Worst fall", pct(bt.max_drawdown, 1), "text-loss"],
        ["Trading cost assumed", `${bt.cost_bp} bp`],
      ]} />
      <p className="text-muted-foreground mt-1 text-xs">Tested {yearDate(bt.period.slice(0, 10))} to {yearDate(bt.period.slice(-10))}, after costs.</p>
      {idea && data.research.replays[idea.id] && (
        <a href={`#research/trial/${idea.id}`} className="hover:text-foreground text-muted-foreground mt-2 inline-flex h-11 items-center gap-1 text-[13px] sm:h-8">
          Watch the backtest run<ChevronRight className="size-3.5" />
        </a>
      )}
    </div>
  );
}

/** Stop or resume, with what happens spelled out, a required reason and a plain confirm. */
function StopResumeDialog({ book, mode, onClose, onDone }: { book: Book; mode: "stop" | "resume" | null; onClose: () => void; onDone: (s: BookStatus) => void }) {
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (mode) { setReason(""); setTried(false); setFailed(false); setBusy(false); } }, [mode]);

  const stop = mode === "stop";
  const next = book.next_run.at!;
  const open = book.orders.filter((o) => o.status === "open" || o.status === "partial").length;
  const missing = reason.trim().length < 3;
  const s = book.status;

  const submit = () => {
    setTried(true);
    if (missing) { field.current?.focus(); return; }
    setBusy(true); setFailed(false);
    setTimeout(() => {
      if (failOnce.left) { failOnce.left = false; setBusy(false); setFailed(true); return; }
      onDone(stop ? { state: "stopped", by: "you", at: new Date().toISOString(), reason: reason.trim() } : { state: "running" });
    }, 700);
  };

  return (
    <Dialog open={!!mode} onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="bg-raised gap-5 sm:max-w-md">
        <DialogHeader className="text-left">
          <DialogTitle>{stop ? `Stop ${book.name}?` : `Resume ${book.name}?`}</DialogTitle>
          <DialogDescription className="sr-only">{stop ? "What stopping does, and a reason to record." : "What resuming does, and a reason to record."}</DialogDescription>
        </DialogHeader>
        <ul className="list-disc space-y-1.5 pl-5 text-[13.5px]">
          {stop ? (
            <>
              <li>No orders from the next run, {at(next)}, or any run after it.</li>
              <li>It keeps {book.positions.length ? <>its {book.positions.length} stocks and <span className="num">{money(book.cash)}</span> cash</> : <><span className="num">{money(book.cash)}</span> in cash</>}. Nothing is sold.</li>
              <li>{open ? `${open} open order${open > 1 ? "s are" : " is"} cancelled.` : "No orders are open, so nothing is cancelled."}</li>
              <li>Its value and chart keep updating. It stays stopped until you resume it.</li>
            </>
          ) : (
            <>
              <li>Stopped by {s.by === "safety" ? `the ${s.rule?.toLowerCase()}` : "you"}{s.at && <>, {at(s.at)}</>}{s.reason && <>: {s.by === "safety" ? s.reason : `“${s.reason}”`}</>}</li>
              <li>Nothing trades now. The next run is {at(next)}.</li>
              <li>That run trades back to the rule: it buys what entered the top and sells what fell out while it was stopped.</li>
              {s.by === "safety" && <li>Resuming doesn't change the {s.rule?.toLowerCase()}; it can stop the book again.</li>}
            </>
          )}
        </ul>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="reason" className="text-[13px] font-medium">{stop ? "Why are you stopping it?" : "Why resume now?"}</label>
          <Textarea id="reason" ref={field} className="aria-invalid:border-attention aria-invalid:ring-attention/25 dark:aria-invalid:ring-attention/30" value={reason} onChange={(e) => setReason(e.target.value)} rows={2} disabled={busy}
            aria-invalid={tried && missing} aria-describedby="reason-help"
            placeholder={stop ? "e.g. earnings week, want to watch first" : "e.g. earnings are out, nothing looks wrong"} />
          <p id="reason-help" className={cn("text-xs", tried && missing ? "text-attention" : "text-muted-foreground")}>
            {tried && missing ? "Write a reason first. It's saved with the change so you can see later why." : "Saved with the change, next to who made it and when."}
          </p>
        </div>
        {failed && (
          <p role="alert" className="text-attention text-[13px]">
            Couldn't {stop ? "stop" : "resume"} {book.name}. Nothing changed: it's still {stop ? "running" : "stopped"}. Try again; if it keeps failing, the computer running TradePartner may be offline.
          </p>
        )}
        <DialogFooter className="gap-2">
          <Button variant="outline" className="h-11 sm:h-9" onClick={onClose} disabled={busy}>{stop ? "Keep it running" : "Leave it stopped"}</Button>
          <Button className="h-11 sm:h-9" onClick={submit} disabled={busy}>{busy ? (stop ? "Stopping…" : "Resuming…") : failed ? "Try again" : stop ? `Stop ${book.name}` : `Resume ${book.name}`}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Tap a holding or an order: the detail, with the reason in the rule's own terms. */
function DetailSheet({ open, book, rule, onClose }: { open: Open; book: Book; rule: Rule; onClose: () => void }) {
  return (
    <Sheet open={!!open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        {open?.kind === "holding" && <HoldingDetail p={open.p} book={book} rule={rule} />}
        {open?.kind === "order" && <OrderDetail o={open.o} />}
      </SheetContent>
    </Sheet>
  );
}

function Rows({ rows }: { rows: [string, React.ReactNode, string?][] }) {
  return (
    <dl className="text-[13px]">
      {rows.map(([k, v, cls]) => (
        <div key={k} className="flex items-baseline justify-between gap-4 border-b py-2.5">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className={cn("num text-right", cls)}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function HoldingDetail({ p, book, rule }: { p: Position; book: Book; rule: Rule }) {
  const isReturn = /return/.test(rule.measure);
  const signal = isReturn ? pct(p.signal, 0) : p.signal.toFixed(2);
  const cut = holdOf(book, rule);
  const next = `${weekdayDate(book.next_run.at!)}`;
  const why = p.rank <= cut
    ? `Ranked ${p.rank} of ${rule.universe.toLocaleString("en-US")} on ${rule.measure} (${signal}) at the last rebalance, inside the top ${cut} the rule holds. The next rebalance, ${next}, ranks it again.`
    : `Ranked ${p.rank} (${signal} on ${rule.measure}), below the top ${cut}.`;
  const trades = book.orders.filter((o) => o.symbol === p.symbol);
  const gainPct = p.price / p.avg_cost - 1;
  return (
    <>
      <SheetHeader className="gap-1 border-b">
        <p className="num text-muted-foreground text-sm">{p.symbol}</p>
        <SheetTitle className="text-lg">{p.name}</SheetTitle>
        <SheetDescription className="text-foreground text-[13.5px]">{why}</SheetDescription>
      </SheetHeader>
      <div className="flex flex-col gap-6 px-4 pb-8">
        <Rows rows={[
          ["Value", money(p.value)],
          ["Shares", p.shares.toLocaleString("en-US")],
          ["Price now", money(p.price)],
          ["Average cost", money(p.avg_cost)],
          ["Gain", `${signedMoney(p.unrealized_pnl)} (${pct(gainPct, 1)})`, toneText[tone(p.unrealized_pnl)]],
          ["Weight, target", `${pctW(p.weight)}, ${pctW(p.target_weight)}`],
          ["Held since", shortDate(p.bought_on)],
        ]} />
        <div>
          <h3 className="mb-1 text-[13px] font-medium">Its orders here</h3>
          {trades.length ? (
            <ul className="text-[13px]">{trades.map((o) => <li key={o.id} className="border-b py-2.5">{shortDate(o.placed_at)}: {o.side === "buy" ? "bought" : "sold"} <span className="num">{o.shares}</span>, {STATUS[o.status].toLowerCase()}. <span className="text-muted-foreground">{o.why}</span></li>)}</ul>
          ) : <p className="text-muted-foreground text-[13px]">Bought when the book started; no orders in the recent list.</p>}
        </div>
      </div>
    </>
  );
}

function OrderDetail({ o }: { o: Order }) {
  return (
    <>
      <SheetHeader className="gap-1 border-b">
        <p className="text-muted-foreground text-sm">{at(o.placed_at)}</p>
        <SheetTitle className="text-lg">{verb(o)} <span className="num">{o.shares} {o.symbol}</span></SheetTitle>
        <SheetDescription className="text-foreground text-[13.5px]">{o.why}.</SheetDescription>
      </SheetHeader>
      <div className="flex flex-col gap-4 px-4 pb-8">
        {o.note && <p className="bg-attention-soft text-[13px] rounded-lg p-3"><span className="text-attention font-medium">{STATUS[o.status]}.</span> {o.note}.</p>}
        <Rows rows={[
          ["Status", STATUS[o.status], o.status === "cancelled" || o.status === "rejected" ? "text-attention font-sans" : "font-sans"],
          ["Shares filled", `${o.filled_shares} of ${o.shares}`],
          ["Filled at", o.fill_price != null ? money(o.fill_price) : "—"],
          ["Value filled", o.fill_price != null ? money(o.fill_price * o.filled_shares) : "—"],
        ]} />
      </div>
    </>
  );
}

/** Loading: the book's real layout in grey. */
export function BookLoading() {
  return (
    <Page rail={<>{[0, 1, 2].map((i) => <Skeleton key={i} className="mb-4 h-10 w-full" />)}</>}>
      <div aria-busy="true" aria-label="Loading the book">
        <Skeleton className="h-4 w-16" />
        <Skeleton className="mt-2 h-10 w-64" />
        <Skeleton className="mt-2 h-4 w-48" />
        <Skeleton className="mt-6 h-[230px] w-full sm:h-[300px]" />
        <Skeleton className="mt-10 h-5 w-28" />
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="mt-3 h-9 w-full" />)}
      </div>
    </Page>
  );
}

function BookNotFound({ id, first }: { id: string; first?: string }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-start gap-3 px-4 py-16">
      <h1 className="text-base font-medium">{first ? `No book called “${id}”` : "No books running yet"}</h1>
      <p className="text-muted-foreground">
        {first ? "Check the name in the address, or pick one of your books." : "When a strategy starts trading on paper, its holdings, orders and stop control show up here."}
      </p>
      <Button size="sm" variant="outline" className="h-11 sm:h-9" asChild><a href={first ? `#books/${first}` : "#research"}>{first ? "Open your books" : "Open research"}</a></Button>
    </div>
  );
}
