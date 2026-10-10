import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { AppData } from "@/lib/types";
import { STAGES, family, lab, num, pp, shortSha, stageIndex, strategy, tabsFor, type Result, type Spec, type Strategy } from "@/lib/lab";
import { clock, pct, shortDate, weekdayDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Panel, PrototypeWrite } from "@/components/Shell";
import { PassMark, Steps, TrackScale } from "@/components/Marks";
import { DsrMaths, TermInfo } from "@/components/Term";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { RunDialog } from "@/screens/StrategyActions";

export const yearDate = (iso: string) => `${shortDate(iso)}, ${iso.slice(0, 4)}`;
const stamp = (iso: string) => `${yearDate(iso)}, ${clock(iso)}`;
const GRADE: Record<string, string> = { SUPPORTED: "supported", MIXED: "mixed", INSUFFICIENT: "insufficient", "NOT SUPPORTED": "not supported" };

/**
 * A strategy's page, laid out like a desktop backtester: the frozen
 * specification on the left, read-only; on the right, tabs for the stages it
 * has reached. The actions are the engine's commands.
 */
export function StrategyPage({ data, id, tab, action }: { data: AppData; id: string; tab?: string; action?: string }) {
  const s = strategy(id);
  if (!s) return <p className="text-muted-foreground mx-auto max-w-md px-4 py-16">No strategy called “{id}”. <a className="underline" href="#strategies">Open the list</a>.</p>;
  const tabs = tabsFor(s);
  const current = tabs.find((t) => t.key === tab)?.key ?? (s.result ? "result" : tabs[0].key);
  const f = family(s.family);
  const canRun = !!s.registered_at && s.spec?.kind === "hypothesis";
  const registered = !!s.registered_at;

  return (
    <div className="mx-auto max-w-[1280px] px-4 pt-3 pb-12 sm:px-7 sm:pt-5">
      <a href="#strategies" className="text-muted-foreground hover:text-foreground -ml-1 inline-flex h-9 items-center gap-1 text-[13px]"><ChevronLeft className="size-3.5" />Strategies</a>
      <header className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-[22px] font-medium tracking-[-0.01em]">{s.name}</h1>
          <p className="text-muted-foreground mt-0.5 max-w-[70ch] text-[13.5px]">{s.summary}</p>
        </div>
        {canRun && <Button asChild className="h-11 px-5 sm:h-9"><a href={`#strategies/${s.id}/run`}>Run a trial…</a></Button>}
        {!registered && s.spec && <Button asChild className="h-11 px-5 sm:h-9"><a href={`#strategies/${s.id}/register`}>Register…</a></Button>}
      </header>
      <StageStepper s={s} />
      {registered && f && (
        <p className="num text-muted-foreground mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px]" aria-label="The family's state">
          <span className="font-sans">{f.name} family</span>
          <span className="inline-flex items-center">N {f.n_trials}<TermInfo k="n_trials" /></span>
          <span className="inline-flex items-center">SR* {num(f.sr_star)}<TermInfo k="sr_star" /></span>
          <span className="inline-flex items-center font-sans">holdout {s.id === "h1-daily" ? "forward" : f.holdout.state}, {f.holdout.spends_used} of {f.holdout.cap} spends<TermInfo k="holdout" /></span>
        </p>
      )}

      <div className={cn("mt-5 grid gap-5", s.spec && "lg:grid-cols-[340px_minmax(0,1fr)] lg:gap-7")}>
        {s.spec && <SpecPanel s={s} spec={s.spec} hideThresholds={current === "result"} />}
        <div className="min-w-0">
          <nav aria-label="Stages reached" className="flex flex-wrap gap-1 border-b pb-px">
            {tabs.map((t) => (
              <a key={t.key} href={`#strategies/${s.id}/${t.key}`} aria-current={t.key === current ? "page" : undefined}
                className={cn("-mb-px inline-flex h-11 items-center border-b-2 px-2 text-[13px] sm:h-10 sm:px-3 sm:text-[13.5px]", t.key === current ? "border-foreground text-foreground font-medium" : "text-muted-foreground hover:text-foreground border-transparent")}>
                {t.label}
              </a>
            ))}
          </nav>
          <div className="pt-5">
            {current === "evidence" && <EvidenceTab s={s} />}
            {current === "runs" && <RunsTab s={s} />}
            {current === "result" && s.result && <ResultTab s={s} r={s.result} data={data} />}
            {current === "holdout" && <HoldoutTab s={s} />}
            {current === "paper" && <PaperTab s={s} data={data} />}
            {current === "decisions" && <DecisionsTab s={s} />}
          </div>
        </div>
      </div>
      <p className="text-muted-foreground mt-10 border-t pt-4 text-xs">
        {s.code} <span className="num">{s.id}</span>. Sample data except where lab.json's note says real.
      </p>
      {action === "run" && canRun && <RunDialog s={s} />}
    </div>
  );
}

/** The five stages as countable steps, the current one marked; Parked and Retired say so instead. */
function StageStepper({ s }: { s: Strategy }) {
  const i = stageIndex(s.stage);
  if (i < 0) return <p className="mt-3 text-[13px]"><span className="text-attention">Parked.</span> {s.parked_reason} {s.unpark_when && <span className="text-muted-foreground">Unparks when {s.unpark_when.charAt(0).toLowerCase() + s.unpark_when.slice(1)}</span>}</p>;
  return (
    <ol className="mt-4 grid max-w-[560px] grid-cols-5 gap-1" aria-label={`Stage: ${STAGES[i].label}`}>
      {STAGES.map((st, k) => (
        <li key={st.key} aria-current={k === i ? "step" : undefined}>
          <span className={cn("block h-1.5 rounded-full", k < i ? "bg-foreground/60" : k === i ? "bg-foreground" : "bg-muted")} />
          <span className={cn("mt-1 block text-xs", k === i ? "text-foreground font-medium" : "text-muted-foreground")}>{st.label}</span>
        </li>
      ))}
    </ol>
  );
}

/** The frozen specification, read-only. On a phone it folds under one line. */
function SpecPanel({ s, spec, hideThresholds }: { s: Strategy; spec: Spec; hideThresholds?: boolean }) {
  const f = family(s.family);
  const body = (
    <div className="flex flex-col gap-4 px-4 pb-4 text-[13px]">
      {s.registered_at ? (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
          <dt className="text-muted-foreground">Registered</dt><dd>{stamp(s.registered_at)}</dd>
          <dt className="text-muted-foreground inline-flex items-center">params_sha256<TermInfo k="params_sha256" /></dt>
          <dd className="num truncate" title={s.params_sha256}>{shortSha(s.params_sha256!)}</dd>
          <dt className="text-muted-foreground">File</dt><dd className="num truncate text-xs leading-5" title={spec.doc_path}>{spec.doc_path}</dd>
        </dl>
      ) : (
        <p className="text-muted-foreground">Not registered. Nothing below is frozen yet; <span className="num">{spec.doc_path}</span> holds it.</p>
      )}
      <div>
        <h3 className="text-muted-foreground mb-1 text-xs">Frozen parameters</h3>
        <dl className="num text-[12px]">
          {spec.frozen.map(([k, v]) => (
            <div key={k} className="flex flex-wrap justify-between gap-x-3 border-b py-1">
              <dt className="text-muted-foreground break-all">{k}</dt>
              <dd className="ml-auto text-right">{v}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div>
        <h3 className="text-muted-foreground mb-1 text-xs">Windows</h3>
        <dl className="num text-[12px]">
          <div className="flex flex-wrap justify-between gap-x-3 border-b py-1"><dt className="text-muted-foreground">in_sample_start</dt><dd className="ml-auto">{spec.in_sample_start}</dd></div>
          <div className="flex flex-wrap items-center justify-between gap-x-3 border-b py-1"><dt className="text-muted-foreground inline-flex items-center font-sans">Development boundary<TermInfo k="boundary" /></dt><dd className="ml-auto">{lab.development_boundary}</dd></div>
          <div className="flex flex-wrap items-center justify-between gap-x-3 border-b py-1"><dt className="text-muted-foreground inline-flex items-center font-sans">Holdout<TermInfo k="holdout" /></dt><dd className="ml-auto">{spec.holdout.start} to {spec.holdout.end}</dd></div>
        </dl>
        <p className="text-muted-foreground mt-1 text-xs">Holdout kind: {spec.holdout.kind}.</p>
      </div>
      <div>
        <h3 className="text-muted-foreground mb-1 text-xs">Written before any run</h3>
        {hideThresholds ? <p className="text-muted-foreground text-xs">The thresholds lead the Result tab.</p> : (
        <dl className="num text-[12px]">
          {spec.thresholds.map((t) => (
            <div key={t.key} className="flex flex-wrap items-center justify-between gap-x-3 border-b py-1">
              <dt className="text-muted-foreground inline-flex items-center">{t.key}<TermInfo k={t.key} /></dt>
              <dd className="ml-auto text-right">{t.value}{t.on && <span className="text-muted-foreground font-sans"> on {t.on}</span>}</dd>
            </div>
          ))}
        </dl>)}
        <p className="mt-2">{spec.kill}</p>
        <p className="text-muted-foreground mt-2 inline-flex items-center">Trial budget: {spec.budget}<TermInfo k="budget" /></p>
      </div>
      <p className="bg-secondary/70 rounded-md px-3 py-2.5 leading-relaxed">
        Nothing here edits. A changed parameter is a new variant: a new file, registered and counted in the {f?.name.toLowerCase() ?? s.family} family's N.
      </p>
    </div>
  );
  return (
    <aside className="min-w-0 lg:sticky lg:top-4 lg:self-start">
      <Panel title="Frozen specification" className="hidden lg:block">{body}</Panel>
      <details className="bg-raised group rounded-[10px] border lg:hidden">
        <summary className="flex min-h-12 cursor-pointer items-center gap-2 px-4 [&::-webkit-details-marker]:hidden">
          <ChevronRight className="text-muted-foreground size-4 transition-transform group-open:rotate-90" />
          <span className="font-medium">Frozen specification</span>
          <span className="num text-muted-foreground ml-auto truncate text-xs">{s.registered_at ? `${shortDate(s.registered_at)}, ${s.params_sha256!.slice(0, 6)}` : "not registered"}</span>
        </summary>
        {body}
      </details>
    </aside>
  );
}

function EvidenceTab({ s }: { s: Strategy }) {
  return (
    <div className="max-w-[760px]">
      {s.prior && <p><span className="text-muted-foreground">Prior, written before any run:</span> {s.prior}</p>}
      {s.waits_on && !s.registered_at && <p className="mt-2"><span className="text-muted-foreground">Waits on:</span> {s.waits_on.text}.</p>}
      {s.blocked && <p className="text-muted-foreground mt-1 text-[13px]">{s.blocked}</p>}
      <h2 className="mt-6 mb-1 text-[15px] font-medium">Claims it rests on</h2>
      <ul className="border-t">
        {(s.evidence ?? []).map((c) => (
          <li key={c.id} className="grid grid-cols-[52px_96px_minmax(0,1fr)] items-baseline gap-x-3 border-b py-2.5 text-[13.5px] max-sm:grid-cols-[52px_minmax(0,1fr)]">
            <span className="num text-muted-foreground text-xs">{c.id}</span>
            <span className={cn("text-[13px] max-sm:hidden", c.grade === "SUPPORTED" ? "text-gain" : c.grade === "NOT SUPPORTED" ? "text-loss" : "text-muted-foreground")}>{GRADE[c.grade] ?? c.grade}</span>
            <span className="min-w-0">{c.text}<span className="text-muted-foreground sm:hidden"> ({GRADE[c.grade]})</span></span>
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground mt-2 text-xs">From docs/research/claims.toml, graded there. A finished test adds a TP- claim here, good or bad.</p>
    </div>
  );
}

const STATUS_WORD: Record<string, string> = { ok: "ok", failed: "failed" };

function RunsTab({ s }: { s: Strategy }) {
  const rows = s.trials ?? [];
  const isSweep = s.spec?.kind === "sweep";
  return (
    <div>
      {rows.length === 0 ? <p className="text-muted-foreground">No trials yet.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-[13px]">
            <thead>
              <tr className="text-muted-foreground border-b text-left text-xs">
                <th className="py-2 pr-3 font-normal">Trial</th>
                <th className="py-2 pr-3 font-normal">Run</th>
                <th className="py-2 pr-3 font-normal">{isSweep ? "Variant" : "What"}</th>
                <th className="py-2 pr-3 font-normal">Status</th>
                <th className="py-2 pr-3 text-right font-normal"><span className="inline-flex items-center">N at run<TermInfo k="n_trials" /></span></th>
                <th className="py-2 pr-3 text-right font-normal">{isSweep ? "dsr_excess" : "DSR"}</th>
                <th className="py-2 text-right font-normal">vs SPY</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t, k) => {
                const refused = t.status.startsWith("refused") || t.status === "needs_gap";
                const d = t.dsr ?? t.dsr_excess;
                return (
                  <tr key={k} className="border-b align-top">
                    <td className="num py-2.5 pr-3">{t.trial_id ?? "–"}</td>
                    <td className="num text-muted-foreground py-2.5 pr-3 whitespace-nowrap">{shortDate(t.run_at)}</td>
                    <td className="py-2.5 pr-3">
                      {t.label}{t.kind === "holdout" && <span className="text-muted-foreground">, holdout</span>}
                      {refused && <p className="num text-muted-foreground mt-1 text-xs leading-snug">{t.message}</p>}
                      {t.note && <p className="text-muted-foreground mt-1 text-xs">{t.note}</p>}
                    </td>
                    <td className={cn("num py-2.5 pr-3", refused && "text-attention")}>{STATUS_WORD[t.status] ?? t.status}</td>
                    <td className="num py-2.5 pr-3 text-right">{t.n_trials ?? ""}</td>
                    <td className="num py-2.5 pr-3 text-right">{d != null ? num(d, d === 0.7262 ? 4 : 2) : ""}</td>
                    <td className="num py-2.5 text-right">{t.excess_cagr_spy != null ? pp(t.excess_cagr_spy) : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-muted-foreground mt-3 max-w-[70ch] text-xs leading-relaxed">
        Every run is a row, refused ones too, with the engine's message word for word. N counts the family's ok in-sample trials, so refused and holdout runs leave it unchanged. No row is ever edited or deleted.
      </p>
    </div>
  );
}

function ResultTab({ s, r, data }: { s: Strategy; r: Result; data: AppData }) {
  const spec = s.spec!;
  return (
    <div className="flex max-w-[860px] flex-col gap-6">
      <section>
        <h2 className="flex flex-wrap items-baseline gap-x-2 text-[15px] font-medium">Pre-registered thresholds<span className="text-muted-foreground text-xs font-normal">frozen {s.registered_at ? yearDate(s.registered_at) : ""}, before the run</span></h2>
        <ul className="num mt-2 flex flex-wrap gap-2 text-[12.5px]">
          {spec.thresholds.map((t) => (
            <li key={t.key} className="bg-secondary/70 inline-flex items-center rounded-md py-1 pr-1 pl-2.5">
              {t.key} <span className="text-foreground ml-1.5 font-medium">{t.value}</span>{t.on && <span className="text-muted-foreground ml-1 font-sans">on {t.on}</span>}<TermInfo k={t.key} />
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2 className="text-[15px] font-medium">{r.title}</h2>
        <p className="num text-muted-foreground mt-0.5 flex flex-wrap gap-x-4 text-[12.5px]">
          <span className="font-sans">finished {stamp(r.finished_at)}</span>
          {r.n_trials !== family(s.family)?.n_trials && <>
            <span className="inline-flex items-center">N at the run {r.n_trials}<TermInfo k="n_trials" /></span>
            <span className="inline-flex items-center">SR* at the run {num(r.sr_star)}<TermInfo k="sr_star"><DsrMaths /></TermInfo></span>
          </>}
          {r.argmax && <span className="font-sans">argmax: <span className="num">{r.argmax}</span></span>}
        </p>
        <ul className="mt-3 border-t">
          {r.checks.map((c) => (
            <li key={c.label} className="grid grid-cols-[20px_minmax(0,1fr)] gap-x-3 border-b py-2.5 sm:grid-cols-[20px_190px_minmax(0,1fr)_minmax(0,1fr)] sm:items-center">
              <PassMark pass={c.pass} />
              <span className="inline-flex items-center font-medium">{c.label}<TermInfo k={c.term}>{c.term === "dsr" && <DsrMaths />}</TermInfo></span>
              <span className="num text-[13px] max-sm:col-start-2">{c.value}{c.against && <span className="text-muted-foreground"> against {c.against}</span>}</span>
              <span className="text-muted-foreground text-[13px] max-sm:col-start-2">{c.read}</span>
            </li>
          ))}
        </ul>
        <CostTiles r={r} />
      </section>

      <Verdict s={s} r={r} />

      <section>
        <h2 className="mb-2 text-[15px] font-medium">{r.kind === "sweep" ? "The variants against the promotion floor" : "Excess over SPY across the in-sample window"}</h2>
        {r.kind === "sweep" ? <VariantPlot r={r} /> : <ExcessChart data={data} />}
      </section>

      <details className="group rounded-[10px] border">
        <summary className="flex min-h-12 cursor-pointer items-center gap-2 px-4 [&::-webkit-details-marker]:hidden">
          <ChevronRight className="text-muted-foreground size-4 transition-transform group-open:rotate-90" />
          <span className="font-medium">How it was computed</span>
          <span className="text-muted-foreground ml-auto text-xs">identity, {r.kind === "sweep" ? "the sweep report" : "the workstation view"}</span>
        </summary>
        <div className="flex flex-col gap-4 border-t px-4 py-4 text-[13px]">
          <dl className="num grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-[12.5px]">
            <dt className="text-muted-foreground">code_version</dt><dd>{r.identity.code_version}</dd>
            <dt className="text-muted-foreground">data_cutoff</dt><dd>{r.identity.data_cutoff}</dd>
            <dt className="text-muted-foreground">params_sha256</dt><dd>{r.identity.params_sha256}</dd>
          </dl>
          {r.variants && (
            <table className="num w-full text-[12.5px]">
              <thead><tr className="text-muted-foreground border-b text-left"><th className="py-1.5 font-normal">variant</th><th className="py-1.5 text-right font-normal">excess_cagr_spy</th><th className="py-1.5 text-right font-normal">dsr_excess</th></tr></thead>
              <tbody>{r.variants.map((v) => <tr key={v.label} className="border-b"><td className="py-1.5">{v.label}{v.argmax && <span className="text-muted-foreground font-sans">, argmax</span>}</td><td className="py-1.5 text-right">{pp(v.excess)}</td><td className="py-1.5 text-right">{num(v.dsr_excess)}</td></tr>)}</tbody>
            </table>
          )}
          {s.id === "h1-momentum-12-1" && (
            <a href={`#strategies/${s.id}/bench`} className="hover:text-foreground text-muted-foreground inline-flex h-10 items-center gap-1">Open the workstation view: the rebalances, step by step<ChevronRight className="size-3.5" /></a>
          )}
          <p className="text-muted-foreground text-xs">It teaches; it does not decide. Every value above is a stored engine field ({r.kind === "sweep" ? "sweep report, trial_results, trial_metrics" : "trial_results, trial_metrics, trial_equity, trial_rebalances"}).</p>
        </div>
      </details>
    </div>
  );
}

/** Each cost level as a tile: ✓ when excess over SPY stays above zero, ✕ when not. */
function CostTiles({ r }: { r: Result }) {
  return (
    <div className="mt-4">
      <h3 className="mb-2 inline-flex items-center text-[13px] font-medium">Cost sensitivity<TermInfo k="cost_sensitivity" /></h3>
      <ol className="grid gap-1.5 sm:grid-cols-5">
        {r.costs.map((c) => (
          <li key={c.bp} className={cn("flex items-center gap-3 rounded-md border px-3 py-2 sm:block sm:px-2", c.base && "border-foreground/60")}>
            <span className="flex items-center justify-between gap-1 max-sm:order-last max-sm:ml-auto"><span className="num text-muted-foreground text-xs max-sm:hidden">{c.bp} bp</span><PassMark pass={c.excess > 0} /></span>
            <span className="num text-muted-foreground w-14 text-xs sm:hidden">{c.bp} bp</span>
            <span className="num block text-[13px] sm:mt-1">{pp(c.excess)}</span>
            {c.base && <span className="text-muted-foreground block text-[11px]">base</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** What the pre-registered rule decides now; the owner agrees or disagrees with a reason. */
function Verdict({ s, r }: { s: Strategy; r: Result }) {
  const [mode, setMode] = useState<"idle" | "disagree" | "agreed" | "recorded">("idle");
  const [reason, setReason] = useState("");
  return (
    <section className="bg-raised rounded-[10px] border p-4">
      <h2 className="text-muted-foreground text-[13px]">What the pre-registered rule decides</h2>
      <p className="mt-1 text-[16px] font-medium">{r.verdict.text}</p>
      <p className="text-muted-foreground mt-1">{r.verdict.then}</p>
      {mode === "idle" && (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button className="h-11 sm:h-9" onClick={() => setMode("agreed")}>Agree</Button>
          <Button variant="outline" className="h-11 sm:h-9" onClick={() => setMode("disagree")}>Disagree, with a reason…</Button>
        </div>
      )}
      {mode === "agreed" && <p role="status" className="mt-3 text-[13px]">Recorded as agreed. (Prototype: nothing was written.)</p>}
      {mode === "recorded" && <p role="status" className="mt-3 text-[13px]">Your disagreement is recorded with its reason. (Prototype: nothing was written.)</p>}
      {mode === "disagree" && (
        <div className="mt-4 flex flex-col gap-2">
          <label htmlFor="disagree" className="text-[13px] font-medium">Why you disagree</label>
          <Textarea id="disagree" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Recorded in owner_decisions beside the rule's verdict" />
          <div className="flex gap-2">
            <Button variant="outline" className="h-11 sm:h-9" onClick={() => setMode("idle")}>Cancel</Button>
            <Button className="h-11 sm:h-9" disabled={reason.trim().length < 3} onClick={() => setMode("recorded")}>Record the disagreement</Button>
          </div>
        </div>
      )}
      <div className="mt-4">
        {r.kind === "sweep" ? <PrototypeWrite command={`tradepartner sweep retire ${s.id} --reason "…"`} /> : (
          <p className="text-muted-foreground border-t pt-3 text-xs leading-relaxed">Prototype only, and an engine change: no command records agreement with a single trial's verdict today; it would need an owner_decisions kind.</p>
        )}
      </div>
    </section>
  );
}

/** One row per variant, a dot at its dsr_excess, the promotion floor drawn and labelled on the track. */
function VariantPlot({ r }: { r: Result }) {
  const floor = 0.5;
  const x = (v: number) => `${Math.max(0, Math.min(1, v)) * 100}%`;
  return (
    <figure>
      <div className="relative" role="img" aria-label={`Each variant's dsr_excess against promote_at_least ${floor}: ${r.variants!.map((v) => `${v.label} ${num(v.dsr_excess)}`).join(", ")}.`}>
        <div className="grid grid-cols-[150px_minmax(0,1fr)_64px] items-center gap-x-3 text-[12.5px] max-sm:grid-cols-[110px_minmax(0,1fr)_48px]">
          {r.variants!.map((v) => (
            <div key={v.label} className="contents">
              <span className={cn("num py-1.5 leading-tight", v.argmax ? "text-foreground" : "text-muted-foreground")}>{v.label}</span>
              <span className="relative block h-6">
                <span className="bg-border absolute inset-x-0 top-1/2 h-px" />
                <span className="bg-gain/10 absolute inset-y-0" style={{ left: x(floor), right: 0 }} />
                <span className="bg-foreground/40 absolute inset-y-0 w-px" style={{ left: x(floor) }} />
                <span className={cn("absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full", v.argmax ? "bg-foreground ring-raised ring-2" : "bg-muted-foreground")} style={{ left: x(v.dsr_excess) }} />
              </span>
              <span className="num text-right">{num(v.dsr_excess)}</span>
            </div>
          ))}
          <span />
          <span className="text-muted-foreground relative block h-5 text-[11px]">
            <span className="absolute left-0">0</span>
            <span className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: x(floor) }}>promote_at_least {floor}</span>
            <span className="absolute right-0">1</span>
          </span>
          <span />
        </div>
      </div>
      <figcaption className="text-muted-foreground mt-2 max-w-[70ch] text-xs">
        dsr_excess per variant, recomputed by sweep report at today's N ({r.n_trials}) and the family's SR* high-water mark ({num(r.sr_star_mark ?? r.sr_star)}). The argmax is chosen by excess_cagr_spy, the selection statistic; only the argmax can be promoted.
      </figcaption>
    </figure>
  );
}

/** Cumulative excess over SPY from trial_equity at the base cost level. */
function ExcessChart({ data }: { data: AppData }) {
  const rp = data.research.replays.h1;
  const pts = useMemo(() => {
    if (!rp) return [];
    const v0 = rp.days[0].v, b0 = rp.days[0].b;
    return rp.days.map((d) => ({ date: d.date, e: d.v / v0 - d.b / b0 }));
  }, [rp]);
  if (!pts.length) return null;
  const lo = Math.min(0, ...pts.map((p) => p.e)), hi = Math.max(0, ...pts.map((p) => p.e));
  const W = 600, H = 180;
  const X = (i: number) => (i / (pts.length - 1)) * W;
  const Y = (v: number) => H - ((v - lo) / (hi - lo || 1)) * H;
  const d = pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)} ${Y(p.e).toFixed(1)}`).join("");
  const end = pts.at(-1)!;
  const years = [...new Set(pts.map((p) => p.date.slice(0, 4)))].slice(1);
  return (
    <figure>
      <p className="num mb-1 text-right text-[12.5px]"><span className="text-muted-foreground font-sans">ends at </span><span className={end.e >= 0 ? "text-gain" : "text-loss"}>{pct(end.e, 1)}</span></p>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-[180px] w-full" role="img" aria-label={`Excess over SPY ends at ${pct(end.e, 1)} after ${pts.length} sessions.`}>
          <line x1="0" x2={W} y1={Y(0)} y2={Y(0)} className="stroke-border" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          {years.map((y) => { const i = pts.findIndex((p) => p.date.startsWith(y)); return <line key={y} x1={X(i)} x2={X(i)} y1="0" y2={H} className="stroke-border" strokeDasharray="2 4" vectorEffect="non-scaling-stroke" />; })}
          <path d={d} fill="none" className={end.e >= 0 ? "stroke-gain" : "stroke-loss"} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
        </svg>
        <span className="num text-muted-foreground absolute text-[11px]" style={{ top: `calc(${(Y(0) / H) * 100}% - 16px)`, left: 0 }}>SPY</span>
        <div className="text-muted-foreground relative mt-1 h-4 text-[11px]">
          {years.map((y) => { const i = pts.findIndex((p) => p.date.startsWith(y)); return <span key={y} className="num absolute -translate-x-1/2" style={{ left: `${(X(i) / W) * 100}%` }}>{y}</span>; })}
        </div>
      </div>
      <figcaption className="text-muted-foreground mt-2 max-w-[70ch] text-xs">
        Strategy minus SPY, both from 0 at {yearDate(pts[0].date)}, at 15 bp a side, to the development boundary {yearDate(lab.development_boundary)}. The holdout is not drawn: this is an in-sample trial. Sample curve, calibrated to the trial's recorded result.
      </figcaption>
    </figure>
  );
}

function HoldoutTab({ s }: { s: Strategy }) {
  const f = family(s.family)!;
  const spec = s.spec;
  const isVariantSweep = spec?.kind === "sweep";
  const forward = s.id === "h1-daily";
  return (
    <div className="flex max-w-[760px] flex-col gap-5">
      <div>
        <p className="text-[16px] font-medium">
          {forward ? "Forward holdout, judged by the paper book" : f.holdout.state === "spent" ? `Spent ${f.holdout.spent_by === s.id ? "by this strategy" : `by ${strategy(f.holdout.spent_by!)?.name ?? f.holdout.spent_by}`}, ${yearDate(f.holdout.spent_at!)}` : "Unspent"}
        </p>
        {spec && <p className="num text-muted-foreground mt-1 text-[13px]">{spec.holdout.start} to {spec.holdout.end}</p>}
        <div className="mt-3 flex items-center gap-3 text-[13px]">
          <span className="text-muted-foreground inline-flex items-center">Family spends<TermInfo k="holdout" /></span>
          <span className="flex gap-1" role="img" aria-label={`${f.holdout.spends_used} of ${f.holdout.cap} spends used`}>
            {Array.from({ length: f.holdout.cap }, (_, i) => <span key={i} className={cn("size-3 rounded-sm border", i < f.holdout.spends_used ? "bg-foreground border-foreground" : "border-muted-foreground/50")} />)}
          </span>
          <span className="num text-muted-foreground">{f.holdout.spends_used} of {f.holdout.cap} (max_family_holdout_spends)</span>
        </div>
      </div>

      {s.holdout_result && (
        <div className="border-t pt-4">
          <p>Trial {s.holdout_result.trial_id}: excess over SPY <span className="num">{pp(s.holdout_result.excess_cagr_spy)}</span> a year over the holdout.</p>
          <p className="text-muted-foreground mt-1 text-[13px]">Reason recorded: “{s.holdout_result.reason}”. The holdout run neither retires nor promotes; it is reported.</p>
        </div>
      )}

      {isVariantSweep && <p className="text-muted-foreground border-t pt-4 text-[13px]">Sweep variants never spend the holdout; the engine refuses it (refused_variant). Only a promoted file can, once.</p>}

      {s.holdout_refusal && (
        <div className="border-t pt-4">
          <p className="font-medium">The last spend was refused, so nothing was spent.</p>
          <p className="num border-attention/50 mt-2 rounded-md border px-3 py-2.5 text-[12.5px] leading-relaxed"><span className="text-attention">{s.holdout_refusal.code}</span>: {s.holdout_refusal.message}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button asChild variant="outline" className="h-11 sm:h-9"><a href={`#strategies/${s.id}/override-gap`}>Override the survivorship gap, once…</a></Button>
            <Button asChild className="h-11 sm:h-9"><a href={`#strategies/${s.id}/spend-holdout`}>Spend the holdout…</a></Button>
          </div>
          <p className="text-muted-foreground mt-2 text-xs">Two separate actions, each with its own reason. A spend without the override will be refused again at the same rebalances.</p>
        </div>
      )}
      {!s.holdout_refusal && f.holdout.state === "unspent" && !isVariantSweep && (
        <Button asChild className="h-11 self-start sm:h-9"><a href={`#strategies/${s.id}/spend-holdout`}>Spend the holdout…</a></Button>
      )}
    </div>
  );
}

function PaperTab({ s, data }: { s: Strategy; data: AppData }) {
  const b = data.books.find((x) => x.id === s.book_id);
  const t = lab.tracking.find((x) => x.book_id === s.book_id);
  if (!b || !t) return null;
  return (
    <div className="flex max-w-[760px] flex-col gap-4">
      <p>Book <span className="font-medium">{b.name}</span>, started {weekdayDate(b.started_on)}.</p>
      {t.rebalances_done === 0 ? (
        <p className="text-muted-foreground">No rebalance yet; the first is {weekdayDate(t.first_rebalance!)}. The tracking check starts after it.</p>
      ) : (
        <div className="grid max-w-[460px] grid-cols-[minmax(0,1fr)_72px] items-center gap-x-4 gap-y-1">
          <span className="inline-flex items-center text-[13px]">Tracking gap, through {shortDate(t.through_session)}<TermInfo k="tracking" /></span>
          <span className="num text-right">{pct(t.gap, 1)}</span>
          <TrackScale gap={t.gap} tolerance={t.tolerance} />
          <span className="num text-muted-foreground text-right text-xs">±{(t.tolerance * 100).toFixed(1)}%</span>
        </div>
      )}
      <div className="max-w-[460px]">
        <p className="mb-1.5 inline-flex items-center text-[13px]">{t.forward ? "Forward holdout" : "Toward paper.min_rebalances"}<TermInfo k={t.forward ? "forward" : "tracking"} /></p>
        <Steps done={t.rebalances_done} of={t.min_rebalances} label={t.unit} />
      </div>
      <a href={`#books/${b.id}`} className="text-muted-foreground hover:text-foreground inline-flex h-10 items-center gap-1 text-[13px]">Open the book: holdings, orders, kill switch<ChevronRight className="size-3.5" /></a>
      <p className="text-muted-foreground text-xs">From paper check's tracking line and paper report. The per-period comparison needs the paper_report_periods table (ADR 0018), not built yet.</p>
    </div>
  );
}

function DecisionsTab({ s }: { s: Strategy }) {
  return (
    <div className="max-w-[760px]">
      <ul className="border-t">
        {(s.decisions ?? []).map((d, k) => (
          <li key={k} className="grid grid-cols-[96px_minmax(0,1fr)] gap-x-4 border-b py-3 max-sm:grid-cols-1">
            <span className="num text-muted-foreground text-xs leading-5">{yearDate(d.at)}</span>
            <span className="min-w-0">
              <span className="num text-[13px]">{d.kind}</span>{d.detail && <span className="text-muted-foreground">, {d.detail}</span>}
              <span className="mt-0.5 block">“{d.reason}”</span>
            </span>
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground mt-2 text-xs">owner_decisions rows: every one carries a reason, and none is ever edited.</p>
    </div>
  );
}
