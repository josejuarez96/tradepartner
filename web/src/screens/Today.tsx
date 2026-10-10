import { useMemo, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import type { AppData } from "@/lib/types";
import { lab, strategy, type Health, type Lab } from "@/lib/lab";
import { clock, pct, weekdayDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Panel } from "@/components/Shell";
import { Steps, TrackScale } from "@/components/Marks";
import { TermInfo } from "@/components/Term";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const at = (iso: string) => `${weekdayDate(iso).split(",")[0]} ${clock(iso)}`;
const RUN_WORD: Record<string, string> = { halted: "halted", stale: "skipped on stale data", skipped_kill_switch: "skipped, kill switch engaged", crashed: "crashed", failed: "failed" };

/** Prototype-only states: ?state=alert (a halted run and stale data), ?state=calm (nothing waiting). */
function scenario(base: Lab, state: string | null): Lab {
  const d: Lab = structuredClone(base);
  if (state === "alert") {
    d.health.runs = d.health.runs.map((r) => (r.book_id === "daily" ? { ...r, status: "stale" } : r));
    d.health.alerts = [{ at: "2026-10-09T13:33:00Z", book_id: "daily", kind: "stale_data", detail: "Prices stopped at Thursday's close; the run submitted nothing rather than trade on old numbers." }];
  }
  if (state === "calm") d.waiting = [];
  return d;
}

/**
 * Today, the morning check, phone first: is anything wrong, is anything
 * waiting on me, how is each book doing against its own backtest. Quiet when
 * nothing is wrong. The one write is the kill switch, with a reason.
 */
export function Today({ data }: { data: AppData }) {
  const L = useMemo(() => scenario(lab, new URLSearchParams(location.search).get("state")), []);
  const [kill, setKill] = useState(false);
  const [engaged, setEngaged] = useState<Record<string, { at: string; reason: string }>>({});
  const name = (id: string) => data.books.find((b) => b.id === id)?.name ?? id;

  return (
    <div className="mx-auto max-w-[1080px] px-4 pt-5 pb-12 sm:px-7 sm:pt-8">
      <h1 className="sr-only">Today</h1>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_400px] lg:gap-6">
        <div className="flex min-w-0 flex-col gap-4">
          <Machine h={L.health} name={name} engaged={engaged} />
          <WaitingPanel L={L} />
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <BooksPanel L={L} data={data} engaged={engaged} />
          <div className="flex flex-col gap-1.5 px-1">
            <Button variant="outline" className="border-loss/40 text-loss hover:text-loss hover:bg-loss/10 h-12 justify-center text-[14px] sm:h-10" onClick={() => setKill(true)}>
              Kill switch…
            </Button>
            <p className="text-muted-foreground text-center text-xs">Stops a book's orders at once, from here or the desk. Resume is at the desk.</p>
          </div>
        </div>
      </div>
      <p className="text-muted-foreground mt-10 border-t pt-4 text-xs">Sample data, as of {at(L.as_of)}. Prototype: health and tracking come from the journal's readers through the local API; none is computed in the browser.</p>
      <KillDialog open={kill} books={data.books.map((b) => ({ id: b.id, name: b.name, holds: b.positions.length }))} engaged={engaged}
        onClose={() => setKill(false)} onDone={(ids, reason) => { const t = new Date().toISOString(); setEngaged((m) => ({ ...m, ...Object.fromEntries(ids.map((i) => [i, { at: t, reason }])) })); }} />
    </div>
  );
}

/** Machine health: one sentence when all is well; only the jobs that are not ok, otherwise. */
function Machine({ h, name, engaged }: { h: Health; name: (id: string) => string; engaged: Record<string, unknown> }) {
  const badRuns = h.runs.filter((r) => r.status !== "ok");
  const badRecon = h.reconciliations.filter((r) => r.status !== "ok");
  const ingestBad = h.ingest.status !== "ok";
  // One row per job that is not ok; an alert about a book already listed adds no row.
  const loose = h.alerts.filter((a) => !badRuns.some((r) => r.book_id === a.book_id));
  const problems = badRuns.length + badRecon.length + loose.length + (ingestBad ? 1 : 0);
  const kills = Object.keys(engaged);
  if (!problems) {
    return (
      <div className="px-1 pt-1">
        <p className="text-[22px] font-medium tracking-[-0.01em]">Nothing wrong.</p>
        <p className="text-muted-foreground mt-1 max-w-[56ch]">
          Ingest finished {at(h.ingest.finished_at)}. All {h.runs.length} books ran {weekdayDate(h.runs[0].session).split(",")[0]} morning and reconciled.{" "}
          {kills.length ? <span className="text-attention">Kill switch engaged on {kills.map(name).join(", ")}.</span> : "No alerts."}
        </p>
        <details className="group mt-2">
          <summary className="text-muted-foreground hover:text-foreground inline-flex h-9 cursor-pointer items-center gap-1 text-[13px] [&::-webkit-details-marker]:hidden">
            <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />Each job
          </summary>
          <ul className="mt-1 text-[13px]">
            <JobRow what="Evening ingest" when={at(h.ingest.finished_at)} status={h.ingest.status} />
            {h.runs.map((r) => <JobRow key={r.book_id} what={`${name(r.book_id)}, paper run`} when={at(r.finished_at)} status={r.status} />)}
            {h.reconciliations.map((r) => <JobRow key={r.book_id} what={`${name(r.book_id)}, reconciliation`} when={at(r.at)} status={r.status} />)}
          </ul>
          <p className="text-muted-foreground mt-2 text-xs">Next: {h.next.what.toLowerCase()}, {at(h.next.at)}.</p>
        </details>
      </div>
    );
  }
  return (
    <Panel title={<span className="text-attention">{problems === 1 ? "One thing is wrong" : `${problems} things are wrong`}</span>} className="border-attention/50">
      <ul className="px-4 pb-3">
        {ingestBad && <Problem title={`Evening ingest ${h.ingest.status}`} detail={`Session ${h.ingest.session}`} />}
        {badRuns.map((r) => <Problem key={r.book_id} title={`${name(r.book_id)}: this morning's run ${RUN_WORD[r.status] ?? r.status}`} detail={h.alerts.find((a) => a.book_id === r.book_id)?.detail ?? ""} hold="Nothing was bought or sold. Tonight's ingest usually fixes it; the next run checks again." href={`#books/${r.book_id}`} />)}
        {loose.map((a) => <Problem key={`al-${a.book_id}-${a.at}`} title={`${name(a.book_id)}: ${a.kind.replace("_", " ")}`} detail={a.detail} href={`#books/${a.book_id}`} />)}
        {badRecon.map((r) => <Problem key={`rc-${r.book_id}`} title={`${name(r.book_id)}: reconciliation ${r.status}`} detail="The account and the journal disagree." href={`#books/${r.book_id}`} />)}
      </ul>
      <p className="text-muted-foreground border-t px-4 py-2.5 text-xs">Everything else ran and reconciled.</p>
    </Panel>
  );
}

function JobRow({ what, when, status }: { what: string; when: string; status: string }) {
  return (
    <li className="flex min-h-9 items-center gap-3 border-b py-1.5 last:border-0">
      <span className="min-w-0 flex-1 truncate">{what}</span>
      <span className="num text-muted-foreground text-xs">{when}</span>
      <span className={cn("num w-10 text-right text-xs", status === "ok" ? "text-muted-foreground" : "text-attention")}>{status}</span>
    </li>
  );
}

function Problem({ title, detail, hold, href }: { title: string; detail: string; hold?: string; href?: string }) {
  return (
    <li className="border-b py-2.5 last:border-0">
      <a href={href} className="group block">
        <span className="flex items-center gap-2 font-medium">{title}{href && <ChevronRight className="text-muted-foreground ml-auto size-4" />}</span>
        {detail && <span className="mt-0.5 block text-[13px]">{detail}</span>}
        {hold && <span className="text-muted-foreground mt-0.5 block text-[13px]">{hold}</span>}
      </a>
    </li>
  );
}

function WaitingPanel({ L }: { L: Lab }) {
  if (!L.waiting.length) {
    return <p className="text-muted-foreground px-1">Nothing waiting on you.</p>;
  }
  return (
    <Panel title="Waiting on you" aside={<span className="num text-attention">{L.waiting.length}</span>}>
      <ul className="px-2 pb-2">
        {L.waiting.map((w) => {
          const href = w.pr ? `#strategies/${w.strategy_id}` : `#strategies/${w.strategy_id}${w.tab ? `/${w.tab}` : ""}`;
          return (
            <li key={w.id} className="border-b last:border-0">
              <a href={href} className="hover:bg-secondary/60 focus-visible:ring-ring/50 grid min-h-14 grid-cols-[minmax(0,1fr)_16px] items-center gap-x-2 rounded-md px-2 py-2.5 outline-none focus-visible:ring-2">
                <span className="min-w-0">
                  <span className="block truncate font-medium">{w.text}</span>
                  <span className="text-muted-foreground block truncate text-[13px]">
                    {w.pr ? <>PR <span className="num">#{w.pr.number}</span>, {w.pr.state}, checks {w.pr.checks}{w.sample?.includes("pr") && " (sample)"}</> : w.detail}
                  </span>
                </span>
                <ChevronRight className="text-muted-foreground size-4" />
              </a>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

/** Each book against its own backtest: the tracking gap on a scale whose zone is the tolerance. */
function BooksPanel({ L, data, engaged }: { L: Lab; data: AppData; engaged: Record<string, unknown> }) {
  return (
    <Panel title={<span className="inline-flex items-center gap-1">Books against their backtests <TermInfo k="tracking" /></span>}>
      <ul className="px-2 pb-1">
        {L.tracking.map((t) => {
          const b = data.books.find((x) => x.id === t.book_id);
          const s = strategy(t.strategy_id);
          const killed = !!engaged[t.book_id] || b?.status.state === "stopped";
          return (
            <li key={t.book_id} className="border-b last:border-0">
              <a href={`#books/${t.book_id}`} className="hover:bg-secondary/60 focus-visible:ring-ring/50 grid min-h-14 grid-cols-[minmax(0,1fr)_96px_64px] items-center gap-x-3 rounded-md px-2 py-2 outline-none focus-visible:ring-2">
                <span className="min-w-0">
                  <span className="block truncate font-medium">{b?.name ?? t.book_id}</span>
                  <span className={cn("block truncate text-xs", killed ? "text-attention" : "text-muted-foreground")}>
                    {killed ? "Kill switch engaged" : s?.name}
                  </span>
                </span>
                {t.rebalances_done === 0 ? (
                  <span className="text-muted-foreground col-span-2 text-right text-xs">first rebalance {weekdayDate(t.first_rebalance!).split(", ")[1]}</span>
                ) : (
                  <>
                    <TrackScale gap={t.gap} tolerance={t.tolerance} />
                    <span className={cn("num text-right text-[13px]", Math.abs(t.gap) > t.tolerance && "text-attention")}>{pct(t.gap, 1)}</span>
                  </>
                )}
              </a>
            </li>
          );
        })}
      </ul>
      <p className="text-muted-foreground border-t px-4 py-2.5 text-xs leading-relaxed">
        The marker is the book's return minus its backtest's over the same sessions; the shaded zone is the tolerance. Each book is read against its own backtest, never another book.
      </p>
      {L.tracking.filter((t) => t.forward).map((t) => (
        <div key={t.book_id} className="border-t px-4 py-3">
          <p className="mb-1.5 flex items-center gap-1 text-[13px]">{data.books.find((x) => x.id === t.book_id)?.name}: forward holdout <TermInfo k="forward" /></p>
          <Steps done={t.rebalances_done} of={t.min_rebalances} label={t.unit} />
        </div>
      ))}
    </Panel>
  );
}

/** The kill switch: which book, a required reason, the consequence in one sentence, one button. */
function KillDialog({ open, books, engaged, onClose, onDone }: {
  open: boolean; books: { id: string; name: string; holds: number }[]; engaged: Record<string, unknown>;
  onClose: () => void; onDone: (ids: string[], reason: string) => void;
}) {
  const [pick, setPick] = useState<string>("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const open_ = books.filter((b) => !engaged[b.id]);
  const ids = pick === "all" ? open_.map((b) => b.id) : pick ? [pick] : [];
  const missing = reason.trim().length < 3;
  const label = pick === "all" ? "every book" : books.find((b) => b.id === pick)?.name ?? "";
  const reset = () => { setPick(""); setReason(""); setTried(false); setBusy(false); setDone(null); };
  const submit = () => {
    setTried(true);
    if (!ids.length) return;
    if (missing) { field.current?.focus(); return; }
    setBusy(true);
    setTimeout(() => { onDone(ids, reason.trim()); setBusy(false); setDone(label); }, 600);
  };
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && !busy) { onClose(); reset(); } }}>
      <DialogContent className="bg-raised gap-4 shadow-none sm:max-w-md">
        <DialogHeader className="text-left">
          <DialogTitle className="flex items-center gap-1">Engage the kill switch <TermInfo k="kill_switch" /></DialogTitle>
          <DialogDescription className="text-foreground text-[13.5px]">
            Holdings stay. The next scheduled run submits nothing until you resume at the desk, after a reconciliation with status ok. It is never delayed by a running job.
          </DialogDescription>
        </DialogHeader>
        {done ? (
          <>
            <p role="status" className="text-[14px]">Engaged on {done}. The next run submits nothing. <span className="text-muted-foreground">(Prototype: nothing was sent.)</span></p>
            <DialogFooter><Button className="h-11 sm:h-9" onClick={() => { onClose(); reset(); }}>Done</Button></DialogFooter>
          </>
        ) : (
          <>
            <fieldset className="flex flex-col gap-1" disabled={busy}>
              <legend className="mb-1 text-[13px] font-medium">Which book</legend>
              {[...open_.map((b) => ({ id: b.id, text: b.name, note: b.holds ? `holds ${b.holds} stocks` : "all cash" })), ...(open_.length > 1 ? [{ id: "all", text: "Every book", note: "paper kill --all" }] : [])].map((o) => (
                <label key={o.id} className={cn("flex min-h-11 cursor-pointer items-center gap-3 rounded-md border px-3", pick === o.id ? "border-foreground" : "border-border")}>
                  <input type="radio" name="kill-book" value={o.id} checked={pick === o.id} onChange={() => setPick(o.id)} className="accent-foreground size-4" />
                  <span className="flex-1">{o.text}</span>
                  <span className="num text-muted-foreground text-xs">{o.note}</span>
                </label>
              ))}
              {tried && !ids.length && <p className="text-attention text-xs">Pick a book first.</p>}
            </fieldset>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="kill-reason" className="text-[13px] font-medium">Reason</label>
              <Textarea id="kill-reason" ref={field} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} disabled={busy}
                aria-invalid={tried && missing} className="aria-invalid:border-attention"
                placeholder="e.g. broker outage, want to look before it trades" />
              <p className={cn("text-xs", tried && missing ? "text-attention" : "text-muted-foreground")}>
                {tried && missing ? "A reason is required; it is recorded with the switch." : "Recorded with the switch, beside the time."}
              </p>
            </div>
            <DialogFooter className="gap-2">
              <Button variant="outline" className="h-11 sm:h-9" onClick={() => { onClose(); reset(); }} disabled={busy}>Cancel</Button>
              <Button className="bg-loss hover:bg-loss/90 h-11 text-white sm:h-9" onClick={submit} disabled={busy}>{busy ? "Engaging…" : label ? `Engage on ${label}` : "Engage"}</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
