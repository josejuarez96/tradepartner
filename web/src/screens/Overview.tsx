import { useMemo, useState } from "react";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, CircleCheck, Pause } from "lucide-react";
import type { AppData, Book } from "@/lib/types";
import { RANGES, comparison, lastChange, rangeStart, type RangeKey } from "@/lib/data";
import { clock, money, pct, pts, shortDate, signedMoney, tone, weekdayDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ReturnChart } from "@/components/ReturnChart";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

const toneText = { gain: "text-gain", loss: "text-loss", flat: "text-muted-foreground" } as const;

function Delta({ v, children }: { v: number; children: React.ReactNode }) {
  const t = tone(v);
  const Icon = t === "loss" ? ArrowDownRight : ArrowUpRight;
  return (
    <Badge variant="outline" className={cn("num", toneText[t])}>
      {t !== "flat" && <Icon />}
      {children}
    </Badge>
  );
}

/** Screen 1, "How am I doing?". shadcn dashboard pattern: summary cards, one chart, one table. */
export function Overview({ data }: { data: AppData }) {
  const [range, setRange] = useState<RangeKey>("ALL");
  const [hover, setHover] = useState<string | null>(null);

  const port = data.portfolio.equity;
  const today = lastChange(port);
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
  const next = running[0];
  const waiting = data.alerts.length;
  const gapAll = all.youRet - all.spyRet;

  return (
    <div className="flex flex-col gap-4 py-4 md:gap-6 md:py-6">
      {data.alerts.map((a) => {
        const book = data.books.find((b) => b.id === a.book_id);
        return (
          <div key={a.id} className="px-4 lg:px-6">
            <Alert className="border-attention/40 bg-attention-soft [&>svg]:text-attention">
              <AlertTriangle />
              <AlertTitle>{a.title}</AlertTitle>
              <AlertDescription>
                <p>{a.detail}</p>
                <p><span className="text-foreground font-medium">What to do:</span> {a.todo}</p>
                {book && (
                  <Button asChild size="sm" variant="outline" className="mt-2">
                    <a href={`#books/${book.id}`}>Open {book.name}</a>
                  </Button>
                )}
              </AlertDescription>
            </Alert>
          </div>
        );
      })}

      <div className="grid grid-cols-1 gap-4 px-4 lg:px-6 @xl/main:grid-cols-2 @6xl/main:grid-cols-4 *:data-[slot=card]:shadow-xs">
        <Card className="@container/card">
          <CardHeader>
            <CardDescription>Total value</CardDescription>
            <CardTitle className="num text-2xl font-semibold @[250px]/card:text-3xl">{money(total)}</CardTitle>
            {today && <CardAction><Delta v={today.rel}>{pct(today.rel)}</Delta></CardAction>}
          </CardHeader>
          <CardFooter className="flex-col items-start gap-1 text-sm">
            {today && <div className={cn("num font-medium", toneText[tone(today.rel)])}>{signedMoney(today.abs)} on {weekdayDate(lastDate)}</div>}
            <div className="text-muted-foreground">All {data.books.length} books, new money not counted as gain</div>
          </CardFooter>
        </Card>

        <Card className="@container/card">
          <CardHeader>
            <CardDescription>Return since {shortDate(own[0].date)}</CardDescription>
            <CardTitle className={cn("num text-2xl font-semibold @[250px]/card:text-3xl", toneText[tone(all.youRet)])}>{pct(all.youRet)}</CardTitle>
            <CardAction><Delta v={gapAll}>{gapAll >= 0 ? "+" : "−"}{pts(gapAll)}</Delta></CardAction>
          </CardHeader>
          <CardFooter className="flex-col items-start gap-1 text-sm">
            <div className="font-medium">{tone(gapAll) === "flat" ? "Level with" : gapAll > 0 ? "Ahead of" : "Behind"} the S&amp;P 500</div>
            <div className="text-muted-foreground num">S&amp;P 500 {pct(all.spyRet)} over the same days</div>
          </CardFooter>
        </Card>

        <Card className="@container/card">
          <CardHeader>
            <CardDescription>Needs you</CardDescription>
            <CardTitle className={cn("text-2xl font-semibold @[250px]/card:text-3xl", waiting && "text-attention")}>
              {waiting ? `${waiting} ${waiting === 1 ? "thing" : "things"}` : "Nothing"}
            </CardTitle>
            <CardAction>
              {waiting ? <AlertTriangle className="size-5 text-attention" /> : <CircleCheck className="size-5 text-gain" />}
            </CardAction>
          </CardHeader>
          <CardFooter className="flex-col items-start gap-1 text-sm">
            <div className="font-medium">{waiting ? data.alerts[0].title : "Every book ran as planned"}</div>
            <div className="text-muted-foreground">Prices as of {weekdayDate(data.as_of).split(",")[0]}'s close</div>
          </CardFooter>
        </Card>

        <Card className="@container/card">
          <CardHeader>
            <CardDescription>Next run</CardDescription>
            <CardTitle className="num text-2xl font-semibold @[250px]/card:text-3xl">
              {next ? `${weekdayDate(next.next_run.at).split(",")[0]} ${clock(next.next_run.at)}` : "None"}
            </CardTitle>
          </CardHeader>
          <CardFooter className="flex-col items-start gap-1 text-sm">
            {next && <div className="font-medium">{next.name} · {(next.next_run.what ?? "run").toLowerCase()}</div>}
            {running[1] && <div className="text-muted-foreground">Then {running.slice(1).map((b) => b.name).join(" and ")}</div>}
          </CardFooter>
        </Card>
      </div>

      <div className="px-4 lg:px-6">
        <Card className="@container/card shadow-xs">
          <CardHeader>
            <CardTitle>Return vs the S&amp;P 500</CardTitle>
            <CardDescription className="num">
              <span className="inline-flex items-center gap-1.5"><i className="inline-block h-0.5 w-3 rounded bg-chart-1" />You <b className={cn("font-medium", toneText[tone(youAt)])}>{pct(youAt)}</b></span>
              <span className="mx-3 inline-flex items-center gap-1.5"><i className="inline-block h-px w-3 bg-chart-2" />S&amp;P 500 <b className="text-foreground font-medium">{pct(spyAt)}</b></span>
              <span>{shortDate(from)} – {shortDate(to)}</span>
            </CardDescription>
            <CardAction>
              <ToggleGroup type="single" value={range} onValueChange={(v) => v && setRange(v as RangeKey)} variant="outline" className="*:data-[slot=toggle-group-item]:px-3!">
                {RANGES.map((r) => (
                  <ToggleGroupItem key={r.key} value={r.key} disabled={!available(r.key)} title={available(r.key) ? undefined : "Not enough history yet"}>
                    {r.label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </CardAction>
          </CardHeader>
          <CardContent className="px-2 pt-2 sm:px-6">
            <ReturnChart
              you={cmp.you}
              spy={cmp.spy}
              onScrub={setHover}
              label={`Your return ${pct(cmp.youRet)} against the S&P 500 ${pct(cmp.spyRet)}, ${shortDate(from)} to ${shortDate(lastDate)}.`}
            />
          </CardContent>
        </Card>
      </div>

      <div className="px-4 lg:px-6">
        <Card className="shadow-xs">
          <CardHeader>
            <CardTitle>Books</CardTitle>
            <CardDescription>{running.length} running{data.books.length - running.length ? `, ${data.books.length - running.length} stopped` : ""}</CardDescription>
          </CardHeader>
          <CardContent className="px-0">
            <Table>
              <TableHeader className="bg-muted/50">
                <TableRow>
                  <TableHead className="pl-6">Book</TableHead>
                  <TableHead className="hidden md:table-cell">Status</TableHead>
                  <TableHead className="text-right">Value</TableHead>
                  <TableHead className="text-right">Since start</TableHead>
                  <TableHead className="hidden text-right sm:table-cell">vs S&amp;P 500</TableHead>
                  <TableHead className="hidden pr-6 lg:table-cell">Next run</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.books.map((b) => <BookRow key={b.id} book={b} data={data} />)}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
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
    <TableRow className="cursor-pointer" onClick={() => (location.hash = `books/${book.id}`)}>
      <TableCell className="pl-6">
        <a href={`#books/${book.id}`} className="font-medium hover:underline">{book.name}</a>
        <div className="text-muted-foreground text-xs">{strat?.name} · {book.cadence}</div>
      </TableCell>
      <TableCell className="hidden md:table-cell">
        {stopped ? (
          <Badge variant="outline" className="text-attention"><Pause />Stopped by you {clock(book.status.at!)}</Badge>
        ) : flagged ? (
          <Badge variant="outline" className="text-attention"><AlertTriangle />Needs you</Badge>
        ) : (
          <Badge variant="outline" className="text-muted-foreground"><span className="size-1.5 rounded-full bg-gain" />Running</Badge>
        )}
      </TableCell>
      <TableCell className="num text-right">{money(book.equity.at(-1)!.value)}</TableCell>
      <TableCell className={cn("num text-right", toneText[tone(cmp.youRet)])}>
        {pct(cmp.youRet)}
        <div className="text-muted-foreground text-xs">{young ? `day ${book.equity.length}` : `since ${shortDate(book.started_on)}`}</div>
      </TableCell>
      <TableCell className={cn("num hidden text-right sm:table-cell", young ? "text-muted-foreground" : toneText[tone(gap)])}>
        {young ? "—" : `${gap >= 0 ? "+" : "−"}${pts(gap)}`}
      </TableCell>
      <TableCell className="text-muted-foreground hidden pr-6 lg:table-cell">
        {book.next_run.what} · {shortDate(book.next_run.at)}
      </TableCell>
    </TableRow>
  );
}
