import { useRef, useState, type ReactNode } from "react";
import { ChevronLeft } from "lucide-react";
import { family, lab, num, type Strategy } from "@/lib/lab";
import { clock, weekdayDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { PrototypeWrite } from "@/components/Shell";
import { TermInfo } from "@/components/Term";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/**
 * The lab's writes: run a trial, register, spend the holdout, override the
 * survivorship gap. Each one states what happens and what it costs, in the
 * engine's terms, before one button. All are PROTOTYPE ONLY: they need the
 * ADR 0018 amendment (drafted in parallel) and a safety-reviewer pass; the
 * app would run the CLI command as a subprocess and show its answer.
 */

const engineParam = new URLSearchParams(location.search).get("engine");

/**
 * The engine's answers, word for word, as `backtest/holdout.decide` writes
 * them. The prototype shows `run` unless ?engine= picks another.
 */
const ANSWERS: Record<string, { outcome: string; message: string }> = {
  run: { outcome: "run", message: "in-sample run" },
  refused_window: { outcome: "refused_window", message: "window end 2024-01-31 is after the development boundary 2023-12-29; in-sample runs read no session after it" },
  refused_variant: { outcome: "refused_variant", message: "this hypothesis is a sweep variant: variants run only through `sweep run`" },
  refused_holdout: { outcome: "refused_holdout", message: "window touches the holdout [2024-01-01, 2026-09-30]; spending it needs --spend-holdout" },
};

function back(s: Strategy, tab = "result") { location.hash = `#strategies/${s.id}/${tab}`; }

/** Run: a counted trial. The cost (N and SR* moving) is shown before the one button. */
export function RunDialog({ s }: { s: Strategy }) {
  const f = family(s.family)!;
  const spec = s.spec!;
  const [start, setStart] = useState(spec.in_sample_start);
  const [end, setEnd] = useState(lab.development_boundary);
  const [note, setNote] = useState("");
  const [state, setState] = useState<"confirm" | "busy" | "answered">("confirm");
  const answer = ANSWERS[engineParam ?? "run"] ?? ANSWERS.run;
  const counted = answer.outcome === "run";
  return (
    <Dialog open onOpenChange={(o) => !o && state !== "busy" && back(s)}>
      <DialogContent onOpenAutoFocus={(e) => e.preventDefault()} className="bg-raised max-h-[92dvh] gap-4 overflow-y-auto shadow-none sm:max-w-lg">
        <DialogHeader className="text-left">
          <DialogTitle>Run {s.name} as a trial</DialogTitle>
          <DialogDescription className="sr-only">The run's cost in N and SR*, its window, and the engine's answer.</DialogDescription>
        </DialogHeader>
        <div className="bg-secondary/60 rounded-md px-3.5 py-3">
          <p className="text-[15px] leading-snug">
            Becomes trial <span className="num font-medium">{f.n_trials + 1}</span> in the {f.name.toLowerCase()} family. SR* rises from <span className="num font-medium">{num(f.sr_star)}</span> to <span className="num font-medium">{num(f.sr_star_next)}</span>.
          </p>
          <p className="text-muted-foreground mt-1.5 text-[13px] leading-relaxed">
            N goes from {f.n_trials} to {f.n_trials + 1}<TermInfo k="n_trials" />. SR*, the Sharpe expected from the best of {f.n_trials + 1} trials by chance<TermInfo k="sr_star" />, is what every later DSR in this family is deflated against. A refused run is logged and does not count.
          </p>
          <p className="text-muted-foreground mt-1.5 text-xs">Sample values. The new SR* needs a size-S engine function (expected_max_sharpe at N + 1 with the family's V), owner decision 2026-10-10.</p>
        </div>

        <fieldset className="grid grid-cols-2 gap-3" disabled={state !== "confirm"}>
          <label className="flex flex-col gap-1 text-[13px]">
            <span className="font-medium">From</span>
            <Input type="date" value={start} min={spec.in_sample_start} max={lab.development_boundary} onChange={(e) => setStart(e.target.value)} className="num h-11 sm:h-9" />
          </label>
          <label className="flex flex-col gap-1 text-[13px]">
            <span className="inline-flex items-center font-medium">To<TermInfo k="boundary" /></span>
            <Input type="date" value={end} min={spec.in_sample_start} max={lab.development_boundary} onChange={(e) => setEnd(e.target.value)} className="num h-11 sm:h-9" />
          </label>
          <p className="text-muted-foreground col-span-2 -mt-1 text-xs">The window ends at the development boundary, {lab.development_boundary}, at the latest. The holdout is a separate action, never an option here.</p>
          <label className="col-span-2 flex flex-col gap-1 text-[13px]">
            <span className="font-medium">Note <span className="text-muted-foreground font-normal">(optional)</span></span>
            <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="why this run" className="h-11 sm:h-9" />
          </label>
        </fieldset>

        <p className="text-muted-foreground inline-flex items-center text-[13px]">It waits for the next quiet interval, Sunday 11:45 pm (sample), and runs one at a time.<TermInfo k="quiet" /></p>

        {state === "answered" && (
          <div role="status" className={cn("rounded-md border px-3.5 py-3", counted ? "" : "border-attention/50")}>
            <p className="text-muted-foreground inline-flex items-center text-xs">The engine's answer<TermInfo k="refusal" /></p>
            <p className="num mt-1 text-[13px]"><span className={counted ? "" : "text-attention"}>{answer.outcome}</span>: {answer.message}</p>
            <p className="mt-2 text-[13px]">{counted ? `Queued as trial ${f.n_trials + 1}. N becomes ${f.n_trials + 1} when it finishes ok; its result lands on Today.` : "Nothing ran and N is unchanged. The refusal is logged in Runs."}</p>
          </div>
        )}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          {state === "answered" ? (
            <Button className="h-11 sm:h-9" onClick={() => back(s, "runs")}>See it in Runs</Button>
          ) : (
            <>
              <Button variant="outline" className="h-11 sm:h-9" onClick={() => back(s)} disabled={state === "busy"}>Cancel</Button>
              <Button className="h-11 sm:h-9" disabled={state === "busy"} onClick={() => { setState("busy"); setTimeout(() => setState("answered"), 600); }}>
                {state === "busy" ? "Asking the engine…" : `Run trial ${f.n_trials + 1}`}
              </Button>
            </>
          )}
        </div>
        <PrototypeWrite command={`tradepartner backtest ${s.id} --start ${start} --end ${end}`} />
        <p className="text-muted-foreground -mt-2 text-xs">Preview a refusal with ?engine=refused_window or ?engine=refused_variant in the address.</p>
      </DialogContent>
    </Dialog>
  );
}

/** A full-screen action page: back link, title, then the anatomy every decision shares. */
function ActionPage({ s, title, children, tab }: { s: Strategy; title: string; children: ReactNode; tab: string }) {
  return (
    <div className="mx-auto max-w-[720px] px-4 pt-3 pb-16 sm:px-7 sm:pt-5">
      <a href={`#strategies/${s.id}/${tab}`} className="text-muted-foreground hover:text-foreground -ml-1 inline-flex h-9 items-center gap-1 text-[13px]"><ChevronLeft className="size-3.5" />{s.name}</a>
      <h1 className="text-[22px] font-medium tracking-[-0.01em]">{title}</h1>
      <div className="mt-5 flex flex-col gap-6">{children}</div>
    </div>
  );
}

function Block({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <section>
      <h2 className="text-muted-foreground mb-2 text-[13px]">{title}</h2>
      {children}
    </section>
  );
}

function Reason({ id, label, value, onChange, tried, hint, disabled }: { id: string; label: string; value: string; onChange: (v: string) => void; tried: boolean; hint: string; disabled?: boolean }) {
  const missing = value.trim().length < 3;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-medium">{label}</label>
      <Textarea id={id} rows={3} value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} aria-invalid={tried && missing} className="aria-invalid:border-attention" />
      <p className={cn("text-xs", tried && missing ? "text-attention" : "text-muted-foreground")}>{tried && missing ? "A reason is required." : hint}</p>
    </div>
  );
}

/** Registration: preview the frozen values exactly as `sweep register` would freeze them. */
export function RegisterScreen({ s }: { s: Strategy }) {
  const spec = s.spec!;
  const f = family(s.family);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <ActionPage s={s} title={`Register ${s.name}`} tab="evidence">
      <p className="bg-secondary/60 rounded-md px-3.5 py-3 text-[15px] leading-snug">
        From here these parameters cannot change. The engine fingerprints them as params_sha256<TermInfo k="params_sha256" />; a change is a new variant, registered and counted against the budget.
      </p>

      <Block title="What freezes">
        <dl className="num text-[12.5px]">
          {[...spec.frozen, ["in_sample_start", spec.in_sample_start] as [string, string]].map(([k, v]) => (
            <div key={k} className="flex flex-wrap justify-between gap-x-4 border-b py-1.5"><dt className="text-muted-foreground break-all">{k}</dt><dd className="ml-auto text-right">{v}</dd></div>
          ))}
          {spec.thresholds.map((t) => (
            <div key={t.key} className="flex flex-wrap items-center justify-between gap-x-4 border-b py-1.5"><dt className="text-muted-foreground inline-flex items-center">{t.key}<TermInfo k={t.key} /></dt><dd className="ml-auto text-right">{t.value}{t.on && <span className="text-muted-foreground font-sans"> on {t.on}</span>}</dd></div>
          ))}
        </dl>
      </Block>

      <Block title={<span className="inline-flex items-center">Holdout<TermInfo k="holdout" /></span>}>
        <p><span className="num">{spec.holdout.start}</span> to <span className="num">{spec.holdout.end}</span>, {spec.holdout.kind}.</p>
        {f?.holdout.state === "spent" && <p className="text-muted-foreground mt-1 text-[13px]">The {f.name.toLowerCase()} family spent it on {weekdayDate(f.holdout.spent_at!)}, so this strategy has no holdout of record; its paper book would be the evidence.</p>}
      </Block>

      <Block title="The kill criterion and the budget">
        <p>{spec.kill}</p>
        <p className="mt-1 inline-flex items-center">{spec.budget}<TermInfo k="budget" /></p>
        {f && <p className="text-muted-foreground mt-1 text-[13px]">Registering runs nothing: N stays {f.n_trials} until the first trial finishes ok.</p>}
      </Block>

      {s.blocked && (
        <p className="border-attention/50 rounded-md border px-3.5 py-3 text-[13px] leading-relaxed">
          <span className="font-medium">Two steps come first.</span> {s.blocked} And the file enters as a one-value sweep reproducing this block. Prototype preview, shown as if both had landed.
        </p>
      )}

      {done ? (
        <div role="status" className="rounded-md border px-3.5 py-3">
          <p className="text-muted-foreground text-xs">The engine's answer</p>
          <p className="num mt-1 text-[13px]">registered {done}, params_sha256 7d2c91…a40e (sample)</p>
          <p className="mt-2 text-[13px]">It moves to Testing. The next step is a run, which is a trial.</p>
        </div>
      ) : (
        <div className="flex flex-col-reverse gap-2 sm:flex-row">
          <Button variant="outline" className="h-11 sm:h-9" asChild><a href={`#strategies/${s.id}`}>Not yet</a></Button>
          <Button className="h-11 sm:h-9" disabled={busy} onClick={() => { setBusy(true); setTimeout(() => setDone(`${weekdayDate(new Date().toISOString())} ${clock(new Date().toISOString())}`), 600); }}>
            {busy ? "Registering…" : "Register and freeze"}
          </Button>
        </div>
      )}
      <PrototypeWrite command="tradepartner sweep register docs/sweeps/b10-short-term-momentum.md" />
    </ActionPage>
  );
}

/** Kept between the two holdout screens: an override is recorded for the one spend that follows. */
const pending: { gap?: string } = {};

/** Spend the holdout: once per family, on a frozen finalist, with a reason and a typed confirmation. */
export function SpendHoldoutScreen({ s }: { s: Strategy }) {
  const f = family(s.family)!;
  const spec = s.spec!;
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const [tried, setTried] = useState(false);
  const [state, setState] = useState<"form" | "busy" | "done">("form");
  const word = f.id;
  const ok = reason.trim().length >= 3 && typed.trim().toLowerCase() === word;
  const breach = s.holdout_refusal?.code === "refused_gap" && !pending.gap;
  return (
    <ActionPage s={s} title={`Spend the ${f.name.toLowerCase()} family's holdout`} tab="holdout">
      <p className="bg-secondary/60 rounded-md px-3.5 py-3 text-[15px] leading-snug">
        The holdout is out-of-sample data this family has never read. It is spent once, on this frozen finalist. There is no second holdout.
      </p>
      <Block title="What happens">
        <ul className="list-disc space-y-1.5 pl-5">
          <li>{s.name} runs once over <span className="num">{spec.holdout.start}</span> to <span className="num">{spec.holdout.end}</span>, as a holdout trial. N does not change.</li>
          <li>The family uses <span className="num">{f.holdout.spends_used + 1}</span> of its <span className="num">{f.holdout.cap}</span> spends (max_family_holdout_spends). A repeat on this strategy is refused.</li>
          <li>The result is reported, good or bad, and becomes the evidence for or against paper and live.</li>
        </ul>
      </Block>
      {pending.gap && <p className="text-[13px]">With the survivorship-gap override for this run only: “{pending.gap}”.</p>}
      {breach && (
        <p className="border-attention/50 num rounded-md border px-3.5 py-3 text-[12.5px] leading-relaxed">
          <span className="font-sans">The last attempt was refused, and this one will be too without the override:</span><br />
          <span className="text-attention">refused_gap</span>: {s.holdout_refusal!.message}
        </p>
      )}
      <Reason id="spend-reason" label="Why this finalist, now" value={reason} onChange={setReason} tried={tried} hint="Recorded as owner_decisions.holdout_spend, with the trial." disabled={state !== "form"} />
      <div className="flex flex-col gap-1.5">
        <label htmlFor="spend-type" className="text-[13px] font-medium">Type <span className="num">{word}</span> to confirm</label>
        <Input id="spend-type" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" className="num h-11 sm:h-9" disabled={state !== "form"} />
      </div>
      {state === "done" ? (
        <p role="status" className="rounded-md border px-3.5 py-3 text-[13px]">Queued as a holdout trial for the next quiet interval. (Prototype: nothing was sent.)</p>
      ) : (
        <div className="flex flex-col-reverse gap-2 sm:flex-row">
          <Button variant="outline" className="h-11 sm:h-9" asChild><a href={`#strategies/${s.id}/holdout`}>Keep it unspent</a></Button>
          <Button className="h-11 sm:h-9" disabled={state === "busy"} onClick={() => { setTried(true); if (!ok) return; setState("busy"); setTimeout(() => setState("done"), 600); }}>
            {state === "busy" ? "Spending…" : "Spend the holdout, once"}
          </Button>
        </div>
      )}
      {tried && !ok && reason.trim().length >= 3 && <p className="text-attention -mt-4 text-xs">Type {word} exactly.</p>}
      <PrototypeWrite command={`tradepartner backtest ${s.id} --spend-holdout --holdout-reason "…"${pending.gap ? ' --override-gap --gap-reason "…"' : ""}`} />
    </ActionPage>
  );
}

/** Override the survivorship gap: for one run only, with its own reason. */
export function OverrideGapScreen({ s }: { s: Strategy }) {
  const ref = s.holdout_refusal;
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  return (
    <ActionPage s={s} title="Override the survivorship gap for one run" tab="holdout">
      <p className="bg-secondary/60 rounded-md px-3.5 py-3 text-[15px] leading-snug">
        Accepting a survivorship-gap count share above gap.count_share_threshold (0.05) for this run only<TermInfo k="gap" />. The next run is gated again.
      </p>
      {ref && (
        <Block title="Where it is over the threshold">
          <ul className="num border-t text-[13px]">
            {ref.breaches.map(([d, v]) => (
              <li key={d} className="flex justify-between border-b py-2"><span>{d}</span><span><span className="text-attention">{v.toFixed(4)}</span> <span className="text-muted-foreground">against 0.05</span></span></li>
            ))}
          </ul>
          <p className="text-muted-foreground mt-2 text-xs">From trial_rebalances.gap_count_share at the holdout's rebalances: the share of the universe whose delisting the store may have missed.</p>
        </Block>
      )}
      <div ref={box}>
        <Reason id="gap-reason" label="Why the result is still worth reading" value={reason} onChange={setReason} tried={tried} hint="Recorded in owner_decisions with the run it applies to." />
      </div>
      <div className="flex flex-col-reverse gap-2 sm:flex-row">
        <Button variant="outline" className="h-11 sm:h-9" asChild><a href={`#strategies/${s.id}/holdout`}>Don't override</a></Button>
        <Button className="h-11 sm:h-9" onClick={() => { setTried(true); if (reason.trim().length < 3) return; pending.gap = reason.trim(); location.hash = `#strategies/${s.id}/spend-holdout`; }}>
          Override for one run, then spend
        </Button>
      </div>
      <PrototypeWrite command={`tradepartner backtest ${s.id} --spend-holdout --holdout-reason "…" --override-gap --gap-reason "…"`} />
    </ActionPage>
  );
}
